import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

import aioserial
import serial

from fastcs.connections.connection import (
    Connection,
    DisconnectedError,
    IntrospectionT,
    disconnect_on,
)
from fastcs.tracer import Tracer


class SerialConnection(Connection[IntrospectionT], Tracer):
    """A serial link to a device that exchanges terminated messages.

    Args:
        port: The serial port to open
        baud: The baud rate
        bytesize: The number of data bits in each byte
        parity: The parity check: none, even, odd, mark or space
        stopbits: The number of stop bits
        rtscts: Whether to use RTS/CTS hardware flow control
        xonxoff: Whether to use XON/XOFF software flow control
        terminator: Added to the end of each message, and marks the end of each reply
        encoding: The text encoding of messages and replies
        timeout: Seconds to wait for each operation before closing the link
        limit: Most bytes a reply can have, not counting its terminator

    Raises:
        ValueError: If ``terminator`` is empty

    """

    # Reopening a local port is quick, and one gone for longer needs a person
    reconnect_attempts = 10
    reconnect_period = 1.0

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        bytesize: Literal[5, 6, 7, 8] = 8,
        parity: Literal["N", "E", "O", "M", "S"] = "N",
        stopbits: Literal[1, 2] = 1,
        rtscts: bool = False,
        xonxoff: bool = False,
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        timeout: float = 5.0,
        limit: int = 2**16,
    ):
        if not terminator:
            raise ValueError("terminator must not be empty")

        super().__init__()
        self.port = port
        self.baud = baud
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.rtscts = rtscts
        self.xonxoff = xonxoff
        self.terminator = terminator
        self.encoding = encoding
        self.timeout = timeout
        self.limit = limit
        self._stream: aioserial.AioSerial | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the link.

        Raises:
            RuntimeError: If the link is already open
            serial.SerialException: If the port can't be opened, or another process
                holds it

        """
        async with self._lock:
            if self._stream is not None:
                raise RuntimeError(f"{self.port} is already connected")

            self._stream = aioserial.AioSerial(
                port=self.port,
                baudrate=self.baud,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                rtscts=self.rtscts,
                xonxoff=self.xonxoff,
                exclusive=True,
            )

    @disconnect_on(OSError)
    async def write(self, message: str) -> None:
        """Send a message, adding the terminator.

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the port fails, such as an unplugged adapter
            TimeoutError: If sending takes longer than ``timeout``

        """
        data = f"{message}{self.terminator}".encode(self.encoding)
        async with self._exchange() as stream:
            await stream.write_async(data)

        self.log_event("Sent message", message=message)

    @disconnect_on(OSError)
    async def query(self, message: str) -> str:
        """Send a message and read the reply.

        Returns:
            The reply, without its terminator

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the port fails, such as an unplugged adapter
            TimeoutError: If the reply takes longer than ``timeout``
            ValueError: If the reply is longer than ``limit`` or can't be decoded

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

    @disconnect_on(OSError)
    async def write_bytes(self, data: bytes) -> None:
        """Send raw bytes, without a terminator.

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the port fails, such as an unplugged adapter
            TimeoutError: If sending takes longer than ``timeout``

        """
        async with self._exchange() as stream:
            await stream.write_async(data)

        self.log_event("Sent bytes", data=data)

    @disconnect_on(OSError)
    async def query_bytes(self, data: bytes, size: int) -> bytes:
        """Send raw bytes and read a reply of a fixed size.

        Returns:
            The reply, exactly ``size`` bytes long

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the port fails, such as an unplugged adapter
            TimeoutError: If the reply takes longer than ``timeout``

        """
        async with self._exchange() as stream:
            await stream.write_async(data)
            reply = await stream.read_async(size)

        self.log_event("Received query bytes response", query=data, response=reply)
        return reply

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
                    # Discard anything left over, such as a late reply to an earlier
                    # query, so it isn't read as the reply to this one
                    stream.reset_input_buffer()
                    yield stream
            except BaseException as e:
                # Close the link so later calls raise DisconnectedError
                self._stream = None
                stream.close()
                if isinstance(e, serial.SerialException):
                    raise ConnectionError(f"{self.port} failed") from e
                raise
