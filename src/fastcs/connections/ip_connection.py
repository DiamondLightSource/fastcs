import asyncio
from dataclasses import dataclass

from fastcs.connections.connection import Connection
from fastcs.tracer import Tracer


class DisconnectedError(Exception):
    """Raised if the ip connection is disconnected."""

    pass


@dataclass
class IPConnectionSettings:
    ip: str = "127.0.0.1"
    port: int = 25565


@dataclass
class StreamConnection:
    """For reading and writing to a stream."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __post_init__(self):
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()

    async def send_message(self, message) -> None:
        self.writer.write(message.encode("utf-8"))
        await self.writer.drain()

    async def receive_response(self) -> str:
        data = await self.reader.readline()
        return data.decode("utf-8")

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


class IPConnection(Connection[None], Tracer):
    """For connecting to an ip using a `StreamConnection`.

    The settings are given at construction rather than to ``connect``, because the
    framework opens and reopens the link without knowing anything about it. IO
    marks the connection down when the *transport* fails, so everything holding it
    stops and its reconnect task wakes.

    Args:
        settings: Where to connect to
        kwargs: Passed to `Connection` - ``depends_on``, ``reconnect_period``,
            ``max_attempts``

    """

    def __init__(self, settings: IPConnectionSettings | None = None, **kwargs) -> None:
        Connection.__init__(self, **kwargs)
        Tracer.__init__(self)
        self._settings = settings or IPConnectionSettings()
        self.__connection: StreamConnection | None = None

    @property
    def _connection(self) -> StreamConnection:
        if self.__connection is None:
            raise DisconnectedError("Need to call connect() before using IPConnection.")

        return self.__connection

    async def connect(self) -> None:
        reader, writer = await asyncio.open_connection(
            self._settings.ip, self._settings.port
        )
        self.__connection = StreamConnection(reader, writer)

    async def send_command(self, message: str) -> None:
        async with self._connection as connection:
            try:
                await connection.send_message(message)
            except OSError:
                # The socket is gone, rather than the device complaining. Everything
                # holding this connection is now down.
                self.set_disconnected()
                raise

    async def send_query(self, message: str) -> str:
        async with self._connection as connection:
            try:
                await connection.send_message(message)
                response = await connection.receive_response()
            except OSError:
                self.set_disconnected()
                raise

            self.log_event(
                "Received query response",
                query=message.strip(),
                response=response.strip(),
            )
            return response

    async def close(self) -> None:
        if self.__connection is None:
            return

        async with self._connection as connection:
            try:
                await connection.close()
            except ConnectionResetError:
                pass
            finally:
                self.__connection = None
