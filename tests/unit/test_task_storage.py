import uuid

import boto3
import pytest
from moto import mock_s3

from funcx_common.task_storage import (
    ChainedTaskStorage,
    MemoryTaskStorage,
    NullTaskStorage,
    RedisTaskStorage,
    S3TaskStorage,
    StorageException,
    ThresholdedMemoryTaskStorage,
    ThresholdedRedisTaskStorage,
)
from funcx_common.tasks import TaskProtocol, TaskState


class SimpleInMemoryTask(TaskProtocol):
    def __init__(self):
        self.task_id = str(uuid.uuid1())
        self.endpoint = None
        self.status = TaskState.RECEIVED
        self.result = None
        self.result_reference = None


def test_memory_storage_simple():
    memstore = MemoryTaskStorage()
    result = "foo result"
    task = SimpleInMemoryTask()

    b = memstore.store_result(task, result)
    assert b is not None
    assert memstore.get_result(task) == result


def test_chained_storage_first_success():
    memstore1 = MemoryTaskStorage()
    memstore2 = MemoryTaskStorage()

    chain = ChainedTaskStorage(memstore1, memstore2)

    result = "foo result"
    task = SimpleInMemoryTask()

    b = chain.store_result(task, result)
    assert b is True
    assert memstore2.get_result(task) is None
    assert memstore1.get_result(task) == result
    assert chain.get_result(task) == result


# @pytest.mark.skip
def test_thresholded_storage_limit():
    store = ThresholdedMemoryTaskStorage(result_limit_chars=10)

    result = "result"
    task = SimpleInMemoryTask()

    b = store.store_result(task, result)
    assert b is True
    assert store.get_result(task) == result

    result2 = "result too long for char limit"
    task2 = SimpleInMemoryTask()

    with pytest.raises(StorageException):
        b = store.store_result(task2, result2)
    with pytest.raises(StorageException):
        store.get_result(task2)


def test_chained_with_threshold():
    memstore1 = ThresholdedMemoryTaskStorage(result_limit_chars=3)
    memstore2 = MemoryTaskStorage()

    chain = ChainedTaskStorage(memstore1, memstore2)

    result = "foo result"
    task = SimpleInMemoryTask()

    b = chain.store_result(task, result)
    assert b is True
    with pytest.raises(StorageException):
        memstore1.get_result(task)
    assert memstore2.get_result(task) == result
    assert chain.get_result(task) == result


def test_null_storage():
    store = NullTaskStorage()

    result = "result"
    task = SimpleInMemoryTask()

    with pytest.raises(StorageException):
        store.store_result(task, result)

    with pytest.raises(StorageException):
        store.get_result(task)


def test_failing_chain_storage():
    store1 = NullTaskStorage()
    store2 = NullTaskStorage()

    chain = ChainedTaskStorage(store1, store2)

    result = "result"
    task = SimpleInMemoryTask()

    with pytest.raises(StorageException):
        chain.store_result(task, result)
    # maybe_todo : Figure out what the behavior here should be
    # if the storage fails, should it be possible to retrieve
    # the result?
    # with pytest.raises(StorageException):
    #    chain.get_result(task)


def test_redis_variants():
    store1 = ThresholdedRedisTaskStorage(result_limit_chars=1000)
    store2 = RedisTaskStorage()

    result = "Hello World!"
    task1 = SimpleInMemoryTask()
    task2 = SimpleInMemoryTask()

    store1.store_result(task1, result)
    store2.store_result(task2, result)

    assert store1.get_result(task1) == result
    assert task1.result_reference["storage_id"] == store1.storage_id

    assert store2.get_result(task2) == result
    assert task2.result_reference["storage_id"] == store2.storage_id


@pytest.fixture()
def test_bucket_mock():
    with mock_s3():
        res = boto3.client("s3")
        res.create_bucket(Bucket="funcx-test-1")
        yield


@pytest.mark.usefixtures("test_bucket_mock")
def test_s3_task_storage():
    store = S3TaskStorage("funcx-test-1")

    task = SimpleInMemoryTask()
    result = "Hello World!"
    store.store_result(task, result)
    assert store.get_result(task) == result, "Result does not match"


@pytest.mark.usefixtures("test_bucket_mock")
def test_chained_redis_and_s3():
    store1 = ThresholdedRedisTaskStorage(result_limit_chars=3)
    store2 = S3TaskStorage("funcx-test-1")

    chain = ChainedTaskStorage(store1, store2)

    result = "foo result"
    task = SimpleInMemoryTask()

    b = chain.store_result(task, result)
    assert b is True

    assert store2.get_result(task) == result
    assert chain.get_result(task) == result

    with pytest.raises(StorageException):
        store1.get_result(task)


@pytest.mark.usefixtures("test_bucket_mock")
def test_chained_redis_and_s3_no_result():
    store1 = ThresholdedRedisTaskStorage(result_limit_chars=3)
    store2 = S3TaskStorage("funcx-test-1")

    chain = ChainedTaskStorage(store1, store2)
    task = SimpleInMemoryTask()

    with pytest.raises(StorageException):
        store1.get_result(task)
    with pytest.raises(StorageException):
        store2.get_result(task)

    assert chain.get_result(task) is None


@pytest.mark.usefixtures("test_bucket_mock")
def test_backward_compat_chained_redis_and_s3_no_result():
    store1 = ThresholdedRedisTaskStorage(result_limit_chars=3)
    store2 = S3TaskStorage("funcx-test-1")

    chain = ChainedTaskStorage(store1, store2)
    task = SimpleInMemoryTask()

    result = "Hello World!"
    task.result = result

    assert store1.get_result(task) == result
    assert chain.get_result(task) == result
