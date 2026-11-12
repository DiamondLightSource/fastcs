import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aioserial

from fastcs.connections.connection import Connection, DisconnectedError, IntrospectionT
from fastcs.tracer import Tracer


class SerialConnection(Connection[IntrospectionT], Tracer):
    """A serial link to a device that exchanges terminated messages.

    Example::

        connection = SerialConnection("/dev/ttyUSB0", baud=9600)
        await connection.write("R=5")
        reply = await connection.query("R?")

    Args:
        port: The serial port to open
        baud: The baud rate
        terminator: Added to the end of each message, and marks the end of each reply
        encoding: The text encoding of messages and replies
        timeout: Seconds to wait for each operation before closing the link
        limit: Most bytes a reply can have, not counting its terminator

    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        timeout: float = 5.0,
        limit: int = 2**16,
    ):
        super().__init__()
        self.port = port
        self.baud = baud
        self.terminator = terminator
        self.encoding = encoding
        self.timeout = timeout
        self.limit = limit
        self._stream: aioserial.AioSerial | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._stream = aioserial.AioSerial(port=self.port, baudrate=self.baud)

    async def write(self, message: str) -> None:
        """Send a message, adding the terminator.

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If sending takes longer than ``timeout``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        async with self._exchange() as stream:
            await stream.write_async(data)

    async def query(self, message: str) -> str:
        """Send a message and read the reply.

        Returns:
            The reply, without its terminator

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If the reply takes longer than ``timeout``
            ValueError: If the reply is longer than ``limit``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        terminator = self.terminator.encode(self.encoding)
        async with self._exchange() as stream:
            await stream.write_async(data)
            reply = await stream.read_until_async(
                terminator, size=self.limit + len(terminator)
            )
            if not reply.endswith(terminator):
                raise ValueError(f"{self.port} reply is longer than {self.limit} bytes")

        response = reply.removesuffix(terminator).decode(self.encoding)
        self.log_event("Received query response", query=message, response=response)
        return response

    async def write_bytes(self, data: bytes) -> None:
        """Send raw bytes, without a terminator.

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If sending takes longer than ``timeout``

        """
        async with self._exchange() as stream:
            await stream.write_async(data)

    async def query_bytes(self, data: bytes, size: int) -> bytes:
        """Send raw bytes and read a reply of a fixed size.

        Returns:
            The reply, exactly ``size`` bytes long

        Raises:
            DisconnectedError: If the link isn't open
            TimeoutError: If the reply takes longer than ``timeout``

        """
        async with self._exchange() as stream:
            await stream.write_async(data)
            return await stream.read_async(size)

    async def close(self) -> None:
        async with self._lock:
            if self._stream is None:
                return

            stream = self._stream
            self._stream = None
            stream.close()

    @asynccontextmanager
    async def _exchange(self) -> AsyncGenerator[aioserial.AioSerial, None]:
        """Hold the lock for one exchange, closing the link if it fails part way"""
        async with self._lock:
            if self._stream is None:
                raise DisconnectedError(f"{self.port} is not connected")

            stream = self._stream
            try:
                async with asyncio.timeout(self.timeout):
                    yield stream
            except BaseException:
                # A late reply could be left in the stream and read by the next query
                self._stream = None
                stream.close()
                raise
