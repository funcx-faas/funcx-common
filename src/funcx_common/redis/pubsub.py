import logging
import queue
import typing as t

from ..tasks import TaskProtocol, TaskState
from .connection import FuncxRedisConnection

log = logging.getLogger(__name__)

_TASK_CHANNEL_PREFIX = "task_channel_"
_TASK_CHANNEL_PREFIX_LEN = len(_TASK_CHANNEL_PREFIX)
_TASK_QUEUE_PREFIX = "task_queue_"

_ALLOWED_MESSAGE_TYPES = ("pong", "message", "pmessage")


def _channel_name(endpoint_id: str) -> str:
    return f"{_TASK_CHANNEL_PREFIX}{endpoint_id}"


def _channel_name_to_endpoint_id(channel: str) -> str:
    return channel[_TASK_CHANNEL_PREFIX_LEN:]


def _queue_name(endpoint_id: str) -> str:
    return f"{_TASK_QUEUE_PREFIX}{endpoint_id}"


class FuncxRedisPubSub(FuncxRedisConnection):
    """
    This class provides a layer over the Redis lib's `PubSub` functionality to
    push and pop messages into an endpoint_id-specific queue.
    Publishing and getting messages (plus subscribe/unsubscribe) are used to
    send messages via a pubsub channel in the normal case.

    If there is no recipient listening for a message, it is pushed into a queue instead.
    Subscribing pops all messages from the queue and puts them onto the pubsub channel.

    **IMPORTANT**

    Unsubscribing from a redis channel is not a synchronous operation. When
    unsubscribing, ensure clean teardown by calling ``get_final_messages()``.
    """

    def __init__(self, hostname: str, *, port: int = 6379):
        super().__init__(hostname, port=port)
        self.pubsub = self.redis_client.pubsub()

    @property
    def subscribed(self) -> bool:
        return bool(self.pubsub.subscribed)

    @FuncxRedisConnection.log_connection_errors
    def put(self, endpoint_id: str, task: TaskProtocol) -> int:
        """
        Put the task ID into the channel for the endpoint.

        Returns the number of receipients who got the message.
        """
        # update the task object
        task.endpoint = endpoint_id
        task.status = TaskState.WAITING_FOR_EP

        # do the "main" publish step and record the number of recipients
        recipients = self.redis_client.publish(_channel_name(endpoint_id), task.task_id)

        # if there were no recipients for the published task_id, put it into
        # the task queue for that endpoint_id
        # when something subscribes to the endpoint channel, it can be
        # republished from there
        if recipients == 0:
            self.redis_client.rpush(_queue_name(endpoint_id), task.task_id)

        return recipients

    @FuncxRedisConnection.log_connection_errors
    def republish_from_queue(self, endpoint_id: str) -> None:
        """
        Tasks pushed to Redis pubsub channels might have gone unreceived.
        When a new endpoint registers, it should republish tasks from it's queues
        to the pubsub channels.
        """
        # NOTE: this could block for an arbitrarily long period of time
        # if this becomes an issue, we can add a limit and batch the
        # resubmissions
        q = _queue_name(endpoint_id)
        channel = _channel_name(endpoint_id)

        # loop "until" trying to pop an item returns None
        queue_item = self.redis_client.blpop(q, timeout=1)
        while queue_item:
            task_list, task_id = queue_item

            # TODO: fix the fact that this does not check the number of
            # subscribers who received the message
            # because this is called from `subscribe()`, the normal case is for
            # this to be used by a client to get messages sent to itself for
            # later use in `get()` calls
            # if the publish step fails, it's not clear what that would mean
            # however, we could add an LPUSH call here to put the task_id back
            # into the queue
            self.redis_client.publish(channel, task_id)

            # pop the next item
            queue_item = self.redis_client.blpop(q, timeout=1)

    @FuncxRedisConnection.log_connection_errors
    def subscribe(self, endpoint_id: str) -> None:
        channel = _channel_name(endpoint_id)
        log.info("subscribing to %s", channel)

        self.pubsub.subscribe(channel)
        self.republish_from_queue(endpoint_id)

    @FuncxRedisConnection.log_connection_errors
    def unsubscribe(self, endpoint_id: str) -> None:
        channel = _channel_name(endpoint_id)
        log.info("unsubscribing from %s", channel)
        self.pubsub.unsubscribe(channel)

    def _get_message(self, timeout: float) -> t.Optional[dict]:
        # skip any subscribe/unsubscribe messages, but do not use the
        # 'ignore_subscribe_messages' flag because it behaves by returning
        # `None` rather than advancing to the next message
        message = self.pubsub.get_message(timeout=timeout)
        while message is not None and message.get("type") not in _ALLOWED_MESSAGE_TYPES:
            message = self.pubsub.get_message(timeout=timeout)
        return message

    @FuncxRedisConnection.log_connection_errors
    def get(self, timeout: int = 2) -> t.Tuple[str, str]:
        """
        :param timeout: wait time for getting a message, in milliseconds
        :type timeout: int
        """
        if not self.subscribed:
            raise queue.Empty

        message = self._get_message(timeout / 1000)
        if not message:
            raise queue.Empty("Channels empty")

        dest_endpoint = _channel_name_to_endpoint_id(message["channel"])
        task_id = message["data"]

        return dest_endpoint, task_id

    @FuncxRedisConnection.log_connection_errors
    def get_final_messages(
        self, timeout: int = 2
    ) -> t.Generator[t.Tuple[str, str], None, None]:
        """
        Yield back messages via ``get()`` for as long as the pubsub is marked
        as subscribed.
        """
        while self.subscribed:
            try:
                yield self.get(timeout=timeout)
            # ignore empty responses during final consumption, since the whole
            # point is to consume until "the end of the queue"
            # but at that point, `self.subscribed` will become False
            except queue.Empty:
                pass
