import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastcs.connections.connection import Connection, DisconnectedError, IntrospectionT
from fastcs.tracer import Tracer


class IPConnection(Connection[IntrospectionT], Tracer):
    """A TCP link to a device that exchanges terminated messages.

    Example::

        connection = IPConnection("10.0.0.5", 4000)
        await connection.write("R=5")
        reply = await connection.query("R?")

    Args:
        ip: The address to connect to
        port: The port to connect to
        terminator: Added to the end of each message, and marks the end of each reply
        encoding: The text encoding of messages and replies
        timeout: Seconds to wait for each operation before closing the link

    """

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 25565,
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        timeout: float = 5.0,
    ):
        super().__init__()
        self.ip = ip
        self.port = port
        self.terminator = terminator
        self.encoding = encoding
        self.timeout = timeout
        self._streams: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with asyncio.timeout(self.timeout):
            self._streams = await asyncio.open_connection(self.ip, self.port)

    async def write(self, message: str) -> None:
        """Send a message, adding the terminator.

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If sending takes longer than ``timeout``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        async with self._exchange() as (_, writer):
            writer.write(data)
            await writer.drain()

    async def query(self, message: str) -> str:
        """Send a message and read the reply.

        Returns:
            The reply, without its terminator

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If the reply takes longer than ``timeout``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        terminator = self.terminator.encode(self.encoding)
        async with self._exchange() as (reader, writer):
            writer.write(data)
            await writer.drain()
            reply = await reader.readuntil(terminator)

        response = reply.removesuffix(terminator).decode(self.encoding)
        self.log_event("Received query response", query=message, response=response)
        return response

    async def write_bytes(self, data: bytes) -> None:
        """Send raw bytes, without a terminator.

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If sending takes longer than ``timeout``

        """
        async with self._exchange() as (_, writer):
            writer.write(data)
            await writer.drain()

    async def query_bytes(self, data: bytes, size: int) -> bytes:
        """Send raw bytes and read a reply of a fixed size.

        Returns:
            The reply, exactly ``size`` bytes long

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If the reply takes longer than ``timeout``

        """
        async with self._exchange() as (reader, writer):
            writer.write(data)
            await writer.drain()
            return await reader.readexactly(size)

    async def close(self) -> None:
        async with self._lock:
            if self._streams is None:
                return

            _, writer = self._streams
            self._streams = None
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
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
            except BaseException:
                # A late reply could be left in the stream and read by the next query
                self._streams = None
                writer.close()
                raise
