import random
from dataclasses import dataclass

import zmq
import zmq.asyncio


@dataclass
class ZeroMQConnectionSettings:
    ip: str = "127.0.0.1"
    port: int = 1515
    type: int = zmq.SUB
    context: zmq.asyncio.Context | None = None
    identity: str | None = None

    @property
    def endpoint(self):
        endpoint = f"tcp://{self.ip}:{self.port}"
        return endpoint


class ZeroMQConnection:
    CHANNEL_HWM = 2

    POLLIN = zmq.POLLIN

    def __init__(
        self,
        settings: ZeroMQConnectionSettings,
    ):
        self._channel_type = settings.type
        self._context = settings.context or zmq.asyncio.Context.instance()
        self._socket = self._context.socket(self._channel_type)
        self._endpoint = settings.endpoint
        self._identity = settings.identity

        # Set the high water mark
        self._socket.set_hwm(self.CHANNEL_HWM)

        match self._channel_type:
            case zmq.SUB:
                self._socket.setsockopt(zmq.SUBSCRIBE, b"")

            # If the socket type is DEALER, set the identity, chosing a random
            # UUID4 value if not specified
            case zmq.DEALER:
                if self._identity is None:
                    self._identity = (
                        f"{random.randrange(0x10000):04x}-"
                        f"{random.randrange(0x10000):04x}"
                    )

                self._socket.setsockopt(
                    zmq.IDENTITY,
                    self._identity.encode()
                    if isinstance(self._identity, str)
                    else self._identity,
                )

    def full_url(self, uri: str) -> str:
        """Expand endpoint and URI into full URL.

        Args:
            uri: Identifier for a resource for the current connection

        """
        return f"{self._endpoint}/{uri}"

    def _ensure_socket(self):
        """
        Make sure the socket exists before running a command.
        If it does, return the Socket object.
        """
        if self._socket is None:
            raise RuntimeError("Socket not connected")
        return self._socket

    def bind(self):
        self._ensure_socket().bind(self._endpoint)

    def connect(self):
        self._ensure_socket().connect(self._endpoint)

    def close(self):
        self._ensure_socket().close()
        self._socket = None

    async def send(self, data):
        status = 0
        try:
            await self._ensure_socket().send(data, flags=zmq.NOBLOCK)
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
            await self._ensure_socket().send_multipart(send_list, flags=zmq.NOBLOCK)
        except zmq.ZMQError:
            # We get a ZMQError if the detector is not available in NOBLOCK mode
            status = -1
        return status

    async def recv(self):
        msg = await self._ensure_socket().recv()
        return msg

    async def recv_multi(self):
        msg = await self._ensure_socket().recv_multipart()
        return msg

    async def poll(self, timeout=-1):
        pollevts = await self._ensure_socket().poll(timeout)
        return pollevts
