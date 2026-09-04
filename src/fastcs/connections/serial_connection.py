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


class SerialConnection(Connection[None]):
    """A serial connection.

    The settings are given at construction rather than to `connect`, because the
    framework opens and reopens the link without knowing anything about it.

    Args:
        settings: Which port to open, and at what baud rate
        kwargs: Passed to `Connection` - ``depends_on``, ``reconnect_period``,
            ``max_attempts``

    """

    def __init__(self, settings: SerialConnectionSettings, **kwargs) -> None:
        super().__init__(**kwargs)
        self._settings = settings
        self._lock = asyncio.Lock()
        self.__stream: aioserial.AioSerial | None = None

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
        try:
            await self._stream.write_async(message)
        except (OSError, aioserial.SerialException):
            # The port is gone, rather than the device complaining.
            self.set_disconnected()
            raise

    async def _receive_response(self, size):
        try:
            return await self._stream.read_async(size)
        except (OSError, aioserial.SerialException):
            self.set_disconnected()
            raise

    async def close(self) -> None:
        async with self._lock:
            if self.__stream is None:
                return

            self.__stream.close()
            self.__stream = None
