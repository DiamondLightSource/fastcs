import asyncio
import ssl
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import httpx

from fastcs.connections.connection import (
    Connection,
    DisconnectedError,
    IntrospectionT,
    disconnect_on,
)
from fastcs.tracer import Tracer


class HTTPConnection(Connection[IntrospectionT], Tracer):
    """An HTTP link to a device with a JSON API.

    Args:
        url: The base URL that request paths are relative to
        timeout: Seconds to wait to connect, or for each response
        headers: Headers to send with every request
        verify: Whether to check the server's TLS certificate, or a path to a CA
            bundle to check it against

    """

    # Long enough to ride out a slow server restart
    reconnect_attempts = 60
    reconnect_period = 5.0

    def __init__(
        self,
        url: str,
        timeout: float = 5.0,
        headers: dict[str, str] | None = None,
        verify: bool | str = True,
    ):
        super().__init__()
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}
        self.verify = verify
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Create the client and check the server answers.

        Raises:
            RuntimeError: If the link is already open
            ConnectionError: If the server can't be reached
            TimeoutError: If the server doesn't answer within ``timeout``
            FileNotFoundError: If the ``verify`` CA bundle doesn't exist

        """
        async with self._lock:
            if self._client is not None:
                raise RuntimeError(f"{self.url} is already connected")

            verify = self.verify
            if isinstance(verify, str):
                verify = ssl.create_default_context(cafile=verify)

            client = httpx.AsyncClient(
                base_url=self.url,
                timeout=self.timeout,
                headers=self.headers,
                follow_redirects=True,
                verify=verify,
            )
            try:
                # Any response, even an error status, shows the server is reachable
                with _link_errors(f"GET {self.url}"):
                    async with asyncio.timeout(self.timeout):
                        await client.get("")
            except BaseException:
                await client.aclose()
                raise

            self._client = client

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request.

        Returns:
            The parsed JSON reply, or ``None`` if the reply is empty

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the server can't be reached
            TimeoutError: If the reply takes longer than ``timeout``
            httpx.HTTPStatusError: If the reply has an error status
            ValueError: If the reply isn't valid JSON

        """
        return _json(await self._send("GET", path, params=params))

    async def put(self, path: str, json: Any = None) -> Any:
        """Send a PUT request with a JSON body.

        Returns:
            The parsed JSON reply, or ``None`` if the reply is empty

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the server can't be reached
            TimeoutError: If the reply takes longer than ``timeout``
            httpx.HTTPStatusError: If the reply has an error status
            ValueError: If the reply isn't valid JSON

        """
        return _json(await self._send("PUT", path, json=json))

    async def post(self, path: str, json: Any = None) -> Any:
        """Send a POST request with a JSON body.

        Returns:
            The parsed JSON reply, or ``None`` if the reply is empty

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the server can't be reached
            TimeoutError: If the reply takes longer than ``timeout``
            httpx.HTTPStatusError: If the reply has an error status
            ValueError: If the reply isn't valid JSON

        """
        return _json(await self._send("POST", path, json=json))

    async def delete(self, path: str) -> Any:
        """Send a DELETE request.

        Returns:
            The parsed JSON reply, or ``None`` if the reply is empty

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the server can't be reached
            TimeoutError: If the reply takes longer than ``timeout``
            httpx.HTTPStatusError: If the reply has an error status
            ValueError: If the reply isn't valid JSON

        """
        return _json(await self._send("DELETE", path))

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send any request, for replies that aren't JSON.

        Args:
            method: The HTTP method
            path: The path, relative to ``url``
            kwargs: Passed to `httpx.AsyncClient.request`

        Returns:
            The response, whatever its status

        Raises:
            DisconnectedError: If the link isn't open
            ConnectionError: If the server can't be reached
            TimeoutError: If the reply takes longer than ``timeout``

        """
        return await self._send(method, path, **kwargs)

    async def close(self) -> None:
        async with self._lock:
            if self._client is None:
                return

            client = self._client
            self._client = None
            await client.aclose()

    @disconnect_on
    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._client
        if client is None:
            raise DisconnectedError(f"{self.url} is not connected")

        try:
            with _link_errors(f"{method} {self.url}{path}"):
                async with asyncio.timeout(self.timeout):
                    response = await client.request(method, path, **kwargs)
        except ConnectionError:
            # Close the link so later calls raise DisconnectedError. A timeout leaves
            # it open, as a slow request doesn't mean the server has gone
            if self._client is client:
                self._client = None
                await client.aclose()
            raise

        self.log_event(
            "Received response", method=method, path=path, status=response.status_code
        )
        return response


@contextmanager
def _link_errors(request: str) -> Generator[None, None, None]:
    """Raise httpx's link errors as the built-in types the other connections raise"""
    try:
        yield
    except httpx.TimeoutException as e:
        raise TimeoutError(f"{request} timed out") from e
    except httpx.TransportError as e:
        raise ConnectionError(f"{request} failed: {e}") from e


def _json(response: httpx.Response) -> Any:
    response.raise_for_status()
    return response.json() if response.content else None
