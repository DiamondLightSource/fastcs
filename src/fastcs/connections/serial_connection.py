import asyncio

import aioserial

from fastcs.connections.connection import Connection


class NotOpenedError(Exception):
    """If the serial stream is not opened."""

    pass


class SerialConnection(Connection):
    """A serial connection.

    Args:
        port: The serial port to open
        baud: The baud rate

    """

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.__stream = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.__stream = aioserial.AioSerial(port=self.port, baudrate=self.baud)

    @property
    def _stream(self) -> aioserial.AioSerial:
        if self.__stream is None:
            raise NotOpenedError(
                "Need to call connect() before using SerialConnection."
            )

        return self.__stream

    async def send_command(self, message: bytes) -> None:
        async with self._lock:
            await self._send_message(message)

    async def send_query(self, message: bytes, response_size: int) -> bytes:
        async with self._lock:
            await self._send_message(message)
            return await self._receive_response(response_size)

    async def _send_message(self, message):
        await self._stream.write_async(message)

    async def _receive_response(self, size):
        return await self._stream.read_async(size)

    async def close(self) -> None:
        async with self._lock:
            self._stream.close()
            self.__stream = None
