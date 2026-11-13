import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastcs.connections.connection import Connection, DisconnectedError, IntrospectionT
from fastcs.tracer import Tracer


class IPConnection(Connection[IntrospectionT], Tracer):
    """A TCP link to a device that exchanges terminated messages.

    Args:
        ip: The address to connect to
        port: The port to connect to
        terminator: Added to the end of each message, and marks the end of each reply
        encoding: The text encoding of messages and replies
        timeout: Seconds to wait for each operation before closing the link
        limit: Most bytes a reply can have, not counting its terminator

    Raises:
        ValueError: If ``terminator`` is empty

    """

    def __init__(
        self,
        ip: str,
        port: int,
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        timeout: float = 5.0,
        limit: int = 2**16,
    ):
        if not terminator:
            raise ValueError("terminator must not be empty")

        super().__init__()
        self.ip = ip
        self.port = port
        self.terminator = terminator
        self.encoding = encoding
        self.timeout = timeout
        self.limit = limit
        self._streams: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the link.

        Raises:
            RuntimeError: If the link is already open
            OSError: If the device can't be reached
            TimeoutError: If opening takes longer than ``timeout``

        """
        async with self._lock:
            if self._streams is not None:
                raise RuntimeError(f"{self.ip}:{self.port} is already connected")

            async with asyncio.timeout(self.timeout):
                self._streams = await asyncio.open_connection(
                    self.ip, self.port, limit=self.limit
                )

    async def write(self, message: str) -> None:
        """Send a message, adding the terminator.

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the device closes the link
            TimeoutError: If sending takes longer than ``timeout``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        async with self._exchange() as (_, writer):
            writer.write(data)
            await writer.drain()

        self.log_event("Sent message", message=message)

    async def query(self, message: str) -> str:
        """Send a message and read the reply.

        Returns:
            The reply, without its terminator

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the device closes the link
            TimeoutError: If the reply takes longer than ``timeout``
            ValueError: If the reply is longer than ``limit`` or can't be decoded

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        terminator = self.terminator.encode(self.encoding)
        async with self._exchange() as (reader, writer):
            writer.write(data)
            await writer.drain()
            try:
                reply = await reader.readuntil(terminator)
            except asyncio.LimitOverrunError as e:
                raise ValueError(
                    f"{self.ip}:{self.port} reply is longer than {self.limit} bytes"
                ) from e

        response = reply.removesuffix(terminator).decode(self.encoding)
        self.log_event("Received query response", query=message, response=response)
        return response

    async def write_bytes(self, data: bytes) -> None:
        """Send raw bytes, without a terminator.

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the device closes the link
            TimeoutError: If sending takes longer than ``timeout``

        """
        async with self._exchange() as (_, writer):
            writer.write(data)
            await writer.drain()

        self.log_event("Sent bytes", data=data)

    async def query_bytes(self, data: bytes, size: int) -> bytes:
        """Send raw bytes and read a reply of a fixed size.

        Returns:
            The reply, exactly ``size`` bytes long

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the device closes the link
            TimeoutError: If the reply takes longer than ``timeout``

        """
        async with self._exchange() as (reader, writer):
            writer.write(data)
            await writer.drain()
            reply = await reader.readexactly(size)

        self.log_event("Received query bytes response", query=data, response=reply)
        return reply

    async def close(self) -> None:
        async with self._lock:
            if self._streams is None:
                return

            _, writer = self._streams
            self._streams = None
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @asynccontextmanager
    async def _exchange(
        self,
    ) -> AsyncGenerator[tuple[asyncio.StreamReader, asyncio.StreamWriter], None]:
        """Hold the lock for one exchange, closing the link if it fails part way"""
        async with self._lock:
            if self._streams is None:
                raise DisconnectedError(f"{self.ip}:{self.port} is not connected")

            reader, writer = self._streams
            try:
                async with asyncio.timeout(self.timeout):
                    yield reader, writer
            except BaseException as e:
                # A late reply could be left in the stream and read by the next query
                self._streams = None
                writer.close()
                if isinstance(e, asyncio.IncompleteReadError):
                    raise ConnectionError(
                        f"{self.ip}:{self.port} closed the link"
                    ) from e
                raise
