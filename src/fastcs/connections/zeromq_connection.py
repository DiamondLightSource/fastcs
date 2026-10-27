import random
from dataclasses import dataclass

import zmq
import zmq.asyncio as zmq_asyncio
from zmq.utils.strtypes import cast_bytes


@dataclass
class ZeroMQConnectionSettings:
    ip: str = "127.0.0.1"
    port: int = 1515
    type: int = zmq.SUB
    context: zmq_asyncio.Context | None = None
    identity: str | None = None

    @property
    def endpoint(self):
        endpoint = f"tcp://{self.ip}:{self.port}"
        return endpoint


class ZeroMQConnection:
    CHANNEL_HWM = 2
    CHANNEL_TYPE_PAIR = zmq.PAIR
    CHANNEL_TYPE_REQ = zmq.REQ
    CHANNEL_TYPE_SUB = zmq.SUB
    CHANNEL_TYPE_PUB = zmq.PUB
    CHANNEL_TYPE_DEALER = zmq.DEALER
    CHANNEL_TYPE_ROUTER = zmq.ROUTER

    POLLIN = zmq.POLLIN

    def __init__(
        self,
        settings: ZeroMQConnectionSettings,
    ):
        self._channel_type = settings.type
        self._context = settings.context or zmq_asyncio.Context().instance()
        self._socket = self._context.socket(self._channel_type)
        self._endpoint = settings.endpoint

        # Set the high water mark
        self._socket.set_hwm(self.CHANNEL_HWM)

        # If the socket type is DEALER, set the identity, chosing a random
        # UUID4 value if not specified
        if self._channel_type == self.CHANNEL_TYPE_DEALER:
            self._identity = settings.identity or (
                f"{random.randrange(0x10000):04x}-{random.randrange(0x10000):04x}"
            )

            self._socket.setsockopt(zmq.IDENTITY, cast_bytes(self._identity))  # pylint: disable=no-member

    def full_url(self, uri: str) -> str:
        """Expand endpoint and URI into full URL.

        Args:
            uri: Identifier for a resource for the current connection

        """
        return f"{self._endpoint}/{uri}"

    def bind(self, endpoint=None):
        if endpoint:
            self._endpoint = endpoint

        self._socket.bind(self._endpoint)

    def connect(self, endpoint=None):
        if endpoint:
            self._endpoint = endpoint

        self._socket.connect(self._endpoint)

    def close(self):
        self._socket.close()

    async def send(self, data):
        status = 0
        try:
            await self._socket.send(data, flags=zmq.NOBLOCK)
        except zmq.ZMQError:
            # We get a ZMQError if the detector is not available in NOBLOCK mode
            status = -1
        return status

    async def send_multi(self, data):
        send_list = []
        for item in data:
            send_list.append(item)

        status = 0
        try:
            await self._socket.send_multipart(send_list, flags=zmq.NOBLOCK)
        except zmq.ZMQError:
            # We get a ZMQError if the detector is not available in NOBLOCK mode
            status = -1
        return status

    async def recv(self):
        msg = await self._socket.recv()
        return msg

    async def recv_multi(self):
        msg = await self._socket.recv_multipart()
        return msg

    async def poll(self, timeout=-1):
        pollevts = await self._socket.poll(timeout)
        return pollevts
