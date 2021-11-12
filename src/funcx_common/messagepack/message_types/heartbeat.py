import uuid

from ..common import Message, MessageType
from ..exceptions import InvalidMessagePayloadError


class Heartbeat(Message):
    """
    Generic Heartbeat message, sent in both directions between Forwarder and
    Endpoint.
    """

    message_type = MessageType.HEARTBEAT

    def __init__(self, endpoint_id: str) -> None:
        self.endpoint_id: str = endpoint_id

    def get_v0_body(self) -> bytes:
        return self.endpoint_id.encode("ascii")

    @classmethod
    def load_v0_body(cls, buf: bytes) -> "Heartbeat":
        data = buf.decode("ascii")
        try:
            uuid.UUID(data)
        except ValueError as e:
            raise InvalidMessagePayloadError(
                "Heartbeat data does not appear to be a UUID"
            ) from e
        return cls(data)
