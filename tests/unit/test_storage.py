import sys
import uuid

import pytest

from globus_compute_common.task_storage import (
    ImplicitRedisStorage,
    RedisS3Storage,
    StorageException,
    get_default_task_storage,
)
from globus_compute_common.tasks import TaskProtocol, TaskState

try:
    import boto3

    if sys.version_info >= (3, 8):
        from moto import mock_aws as moto_mock
    else:
        # moto v5 doesn't support py37; fall back to v4 with mock_s3
        from moto import mock_s3 as moto_mock

    has_boto = True
except ImportError:
    has_boto = False


class SimpleInMemoryTask(TaskProtocol):
    def __init__(self):
        self.task_id = str(uuid.uuid1())
        self.endpoint = None
        self.status = TaskState.RECEIVED
        self.result = None
        self.result_reference = None
        self.payload = None
        self.payload_reference = None


@pytest.fixture
def test_bucket_mock():
    with moto_mock():
        res = boto3.client("s3")
        res.create_bucket(Bucket="compute-test-1")
        yield


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_default_task_storage_s3(compute_s3_bucket, monkeypatch):
    monkeypatch.setenv("COMPUTE_S3_BUCKET_NAME", compute_s3_bucket)
    monkeypatch.delenv("COMPUTE_REDIS_STORAGE_THRESHOLD", raising=False)

    store = get_default_task_storage()
    assert isinstance(store, RedisS3Storage)
    assert store.redis_threshold == 20000  # default value

    # now set a threshold and confirm it gets picked up
    monkeypatch.setenv("COMPUTE_REDIS_STORAGE_THRESHOLD", "100")
    store = get_default_task_storage()
    assert isinstance(store, RedisS3Storage)
    assert store.redis_threshold == 100

    # now set an invalid threshold value; confirm that it is ignored
    monkeypatch.setenv("COMPUTE_REDIS_STORAGE_THRESHOLD", "foo")
    with pytest.raises(ValueError):
        store = get_default_task_storage()


def test_default_task_storage_redis(monkeypatch):
    # no env vars set -> ImplicitRedisStorage
    monkeypatch.delenv("COMPUTE_S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("COMPUTE_REDIS_STORAGE_THRESHOLD", raising=False)

    store = get_default_task_storage()
    assert isinstance(store, ImplicitRedisStorage)

    # confirm that setting a threshold does not change behavior
    # (because bucket is not set)
    monkeypatch.setenv("COMPUTE_REDIS_STORAGE_THRESHOLD", "100")
    store = get_default_task_storage()
    assert isinstance(store, ImplicitRedisStorage)


# this test technically doesn't need to have boto3 installed, but requiring it ensures
# that in the failure case, we will get clearer messages
@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_default_task_storage_redis_from_threshold(compute_s3_bucket, monkeypatch):
    # bucket=... but threshold=-1 -> ImplicitRedisStorage
    monkeypatch.setenv("COMPUTE_S3_BUCKET_NAME", compute_s3_bucket)
    monkeypatch.setenv("COMPUTE_REDIS_STORAGE_THRESHOLD", "-1")

    store = get_default_task_storage()
    assert isinstance(store, ImplicitRedisStorage)


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_storage_simple(compute_s3_bucket):
    # We are setting threshold of 1000 to force storage into redis
    store = RedisS3Storage(bucket_name=compute_s3_bucket, redis_threshold=1000)
    result = "Hello World!"
    task = SimpleInMemoryTask()

    store.store_result(task, result)
    assert store.get_result(task) == result
    assert task.result == result
    assert task.result_reference
    assert task.result_reference["storage_id"] == "redis"


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_backward_compat(compute_s3_bucket):
    store = RedisS3Storage(bucket_name=compute_s3_bucket, redis_threshold=1000)
    result = "Hello World!"
    task = SimpleInMemoryTask()
    task.result = result

    assert store.get_result(task) == result


@pytest.mark.xfail(reason="This will fail until we remove backward compat support")
def test_bad_reference(compute_s3_bucket):
    store = RedisS3Storage(bucket_name=compute_s3_bucket, redis_threshold=1000)
    result = "Hello World!"
    task = SimpleInMemoryTask()
    task.result = result

    # task.result_reference = {'storage_id': 'BAD'}
    print(store.get_result(task))
    with pytest.raises(StorageException):
        store.get_result(task)


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_no_result(compute_s3_bucket):
    """Confirm get_result returns None when there's no result"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name=compute_s3_bucket, redis_threshold=0)
    task = SimpleInMemoryTask()

    assert store.get_result(task) is None


@pytest.mark.skipif(has_boto, reason="test only runs without boto3 lib")
def test_cannot_create_storage_without_boto3_lib(compute_s3_bucket):
    with pytest.raises(RuntimeError):
        # can't create a storage
        RedisS3Storage(bucket_name=compute_s3_bucket, redis_threshold=0)


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_s3_storage_simple_payload(test_bucket_mock):
    """Confirm that payload data is stored to s3(mock)"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=0)
    payload = "Hello World!"
    task = SimpleInMemoryTask()

    store.store_payload(task, payload)
    assert store.get_payload(task) == payload
    assert task.payload_reference
    assert task.payload_reference["storage_id"] == "s3"


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_s3_storage_simple(test_bucket_mock):
    """Confirm that data is stored to s3(mock)"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=0)
    result = "Hello World!"
    task = SimpleInMemoryTask()

    store.store_result(task, result)
    assert store.get_result(task) == result
    assert task.result_reference
    assert task.result_reference["storage_id"] == "s3"


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_differentiator(test_bucket_mock):
    """Confirm that the threshold works to pick the right storage target"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=5)

    result1 = "Hi"
    result2 = "Hello World!"
    task1 = SimpleInMemoryTask()
    task2 = SimpleInMemoryTask()

    store.store_result(task1, result1)
    store.store_result(task2, result2)

    assert store.get_result(task1) == result1
    assert task1.result == result1
    assert task1.result_reference["storage_id"] == "redis"

    assert store.get_result(task2) == result2
    assert task2.result_reference["storage_id"] == "s3"


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
@pytest.mark.parametrize(
    "storage_attrs",
    [
        {"s3bucket": {"do": "del"}, "key": {"do": "del"}},
        {"s3bucket": {"do": "del"}},
        {"key": {"do": "del"}},
        {"s3bucket": {"do": "set", "val": None}},
        {"key": {"do": "set", "val": None}},
    ],
)
def test_s3_task_with_invalid_reference(test_bucket_mock, storage_attrs):
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=0)

    payload = "Hello Payload"
    result = "Hello World!"
    task = SimpleInMemoryTask()
    store.store_payload(task, payload)
    store.store_result(task, result)

    assert task.result_reference["storage_id"] == "s3"
    for key, action in storage_attrs.items():
        if action["do"] == "del":
            del task.result_reference[key]
        elif action["do"] == "set":
            task.result_reference[key] = action["val"]

    with pytest.raises(StorageException):
        store.get_result(task)

    for key, action in storage_attrs.items():
        if action["do"] == "del":
            del task.payload_reference[key]
        elif action["do"] == "set":
            task.payload_reference[key] = action["val"]

    with pytest.raises(StorageException):
        store.get_payload(task)


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_task_with_unknown_storage(test_bucket_mock):
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=0)

    result = "Hello World!"
    task = SimpleInMemoryTask()
    store.store_result(task, result)
    assert task.result_reference["storage_id"] == "s3"
    task.result_reference["storage_id"] = "UnknownFakeStorageType"

    with pytest.raises(StorageException):
        store.get_result(task)


def test_storage_exception_str():
    err = StorageException("foo")
    assert str(err).endswith("reason: foo")


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_differentiator_payload(test_bucket_mock):
    """Confirm that the threshold works to pick the right storage target
    for payloads"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=5)

    payload1 = "Hi"
    payload2 = "Hello World!"
    task1 = SimpleInMemoryTask()
    task2 = SimpleInMemoryTask()

    store.store_payload(task1, payload1)
    store.store_payload(task2, payload2)

    assert store.get_payload(task1) == payload1
    assert task1.payload == payload1
    assert task1.payload_reference["storage_id"] == "redis"

    assert store.get_payload(task2) == payload2
    assert task2.payload_reference["storage_id"] == "s3"


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_internal(test_bucket_mock):
    """Test internal methods"""
    # We are setting threshold of 0 to force only s3 storage
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=5)

    payload1 = "Hi"
    result1 = "Hi"
    payload2 = "Hello World!"
    result2 = "Hello World!"
    task1 = SimpleInMemoryTask()
    task2 = SimpleInMemoryTask()
    from globus_compute_common.task_storage.s3 import StorageFieldName

    store._store_to_s3(task1, StorageFieldName.payload, payload1)
    store._store_to_s3(task1, StorageFieldName.result, result1)
    store._store_to_s3(task2, StorageFieldName.payload, payload2)
    store._store_to_s3(task2, StorageFieldName.result, result2)

    assert store._get_from_s3(task1, StorageFieldName.payload) == payload1
    assert store._get_from_s3(task1, StorageFieldName.result) == result1
    assert store._get_from_s3(task2, StorageFieldName.payload) == payload2
    assert store._get_from_s3(task2, StorageFieldName.result) == result2


@pytest.mark.skipif(not has_boto, reason="test requires boto3 lib")
def test_task_with_unknown_storage_for_payload(test_bucket_mock):
    store = RedisS3Storage(bucket_name="compute-test-1", redis_threshold=0)

    result = "Hello World!"
    task = SimpleInMemoryTask()
    store.store_payload(task, result)
    assert task.payload_reference["storage_id"] == "s3"
    task.payload_reference["storage_id"] = "UnknownFakeStorageType"

    with pytest.raises(StorageException):
        store.get_payload(task)
