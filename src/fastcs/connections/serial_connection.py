import asyncio
from dataclasses import dataclass

import aioserial

from fastcs.connections.connection import Connection


class NotOpenedError(Exception):
    """If the serial stream is not opened."""

    pass


@dataclass
class SerialConnectionSettings:
    port: str
    baud: int = 115200


class SerialConnection(Connection):
    """A serial connection.

    The settings are given at construction rather than to ``connect``, because the
    framework opens and reopens the link without knowing anything about it.

    Args:
        settings: Which port to open, and at what baud rate

    """

    def __init__(self, settings: SerialConnectionSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self.__stream: aioserial.AioSerial | None = None

    @property
    def label(self) -> str:
        return self._settings.port

    async def connect(self) -> None:
        self.__stream = aioserial.AioSerial(
            port=self._settings.port, baudrate=self._settings.baud
        )

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
            if self.__stream is None:
                return

            self.__stream.close()
            self.__stream = None
