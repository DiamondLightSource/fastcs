from dataclasses import dataclass, field
from typing import Any

from httpx import AsyncBaseTransport, AsyncClient, ConnectError, ReadTimeout, Response

from fastcs.connections.connection import Connection
from fastcs.connections.ip_connection import DisconnectedError


@dataclass
class HTTPConnectionSettings:
    host: str = "127.0.0.1"
    port: int = 80
    scheme: str = "http"
    timeout: float = 10.0
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class HTTPConnection(Connection):
    """An HTTP connection.

    The settings are given at construction rather than to ``connect``, because the
    framework opens and reopens the link without knowing anything about it. IO marks
    the connection down when the *transport* fails, so everything holding it stops
    and its reconnect task wakes.

    One framework class rather than one per driver: every REST device does the same
    few things, and a driver needing a verb or a response shape this does not cover
    uses `request` rather than rolling its own client.

    Args:
        settings: Where to connect to
        kwargs: Passed to `Connection` - ``depends_on``, ``reconnect_period``,
            ``reconnect_attempts``

    """

    def __init__(
        self, settings: HTTPConnectionSettings | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or HTTPConnectionSettings()

        self._transport: AsyncBaseTransport | None = None
        """Set by a subclass to talk to an in-process ASGI app instead of a socket.

        Not a constructor argument: a connection's ``__init__`` signature is its
        config schema, and a transport object is not config. It is how a driver
        tests against its own simulator without a network.
        """

        self.__client: AsyncClient | None = None

    @property
    def _client(self) -> AsyncClient:
        if self.__client is None:
            raise DisconnectedError(
                "Need to call connect() before using HTTPConnection."
            )

        return self.__client

    async def connect(self) -> None:
        self.__client = AsyncClient(
            base_url=self._settings.base_url,
            timeout=self._settings.timeout,
            headers=self._settings.headers,
            transport=self._transport,
        )

    async def close(self) -> None:
        if self.__client is None:
            return

        await self.__client.aclose()
        self.__client = None

    async def get(self, path: str) -> Any:
        """GET, returning the parsed JSON body."""
        return (await self.request("GET", path)).json()

    async def get_bytes(self, path: str) -> bytes:
        """GET, returning the raw body. For frame and file data."""
        return (await self.request("GET", path)).content

    async def put(self, path: str, value: Any) -> Any:
        """PUT a JSON value, returning the parsed JSON body if there is one."""
        response = await self.request("PUT", path, json=value)
        return response.json() if response.content else None

    async def request(self, method: str, path: str, **kwargs) -> Response:
        """Every request goes through here.

        The only method that touches connection state, so overriding `get` or `put`
        does not silently change the others, and one failure cannot mark the
        connection down twice.
        """
        try:
            response = await self._client.request(method, path, **kwargs)
        except (ConnectError, ReadTimeout, OSError):
            # The socket is gone, rather than the device complaining. Everything
            # holding this connection is now down.
            self.set_disconnected()
            raise

        # A 4xx or 5xx is a device complaint - it propagates to the caller without
        # touching connection state.
        response.raise_for_status()
        return response
