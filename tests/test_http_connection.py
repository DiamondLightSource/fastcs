import httpx
import pytest
import pytest_asyncio

from fastcs.connections import HTTPConnection, HTTPConnectionSettings
from fastcs.connections.ip_connection import DisconnectedError


def _handler(request: httpx.Request) -> httpx.Response:
    """A device that answers a couple of paths and complains about the rest."""
    if request.url.path == "/value":
        return httpx.Response(200, json={"value": 1.5})
    if request.url.path == "/frame":
        return httpx.Response(200, content=b"\x00\x01\x02")
    if request.url.path == "/set":
        return httpx.Response(200, json={"applied": True})
    if request.url.path == "/nothing":
        return httpx.Response(204)
    return httpx.Response(404, json={"error": "no such parameter"})


class FakeDevice(HTTPConnection):
    """Points the connection at an in-process handler rather than a socket."""

    def __init__(self, handler=_handler, **kwargs) -> None:
        super().__init__(**kwargs)
        self._transport = httpx.MockTransport(handler)


@pytest_asyncio.fixture
async def device():
    connection = FakeDevice()
    await connection.connect()
    yield connection
    await connection.close()


def test_settings_build_the_base_url():
    settings = HTTPConnectionSettings(host="detector", port=8080, scheme="https")

    assert settings.base_url == "https://detector:8080"


def test_the_defaults_are_a_local_http_device():
    assert HTTPConnectionSettings().base_url == "http://127.0.0.1:80"


@pytest.mark.asyncio
async def test_using_it_before_it_is_open_says_so():
    """The same failure as any other connection: not open is not a device fault."""
    with pytest.raises(DisconnectedError, match="connect"):
        await FakeDevice().get("/value")


@pytest.mark.asyncio
async def test_get_returns_the_parsed_body(device: FakeDevice):
    assert await device.get("/value") == {"value": 1.5}


@pytest.mark.asyncio
async def test_get_bytes_returns_the_raw_body(device: FakeDevice):
    """Frame and file data is not JSON."""
    assert await device.get_bytes("/frame") == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_put_returns_the_body_when_there_is_one(device: FakeDevice):
    assert await device.put("/set", {"value": 2.0}) == {"applied": True}


@pytest.mark.asyncio
async def test_put_returns_none_when_the_device_answers_with_nothing(
    device: FakeDevice,
):
    assert await device.put("/nothing", 1) is None


@pytest.mark.asyncio
async def test_an_error_status_is_a_device_complaint_not_a_dead_link(
    device: FakeDevice,
):
    """A 404 is the device rejecting one parameter, so the link stays up."""
    device._set_connected()  # noqa: SLF001

    with pytest.raises(httpx.HTTPStatusError):
        await device.get("/missing")

    assert device.connected


@pytest.mark.asyncio
async def test_a_transport_failure_marks_the_connection_down():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    connection = FakeDevice(refuse)
    await connection.connect()
    connection._set_connected()  # noqa: SLF001

    with pytest.raises(httpx.ConnectError):
        await connection.get("/value")

    assert not connection.connected


@pytest.mark.asyncio
async def test_closing_twice_is_allowed(device: FakeDevice):
    """`close` is called before every reconnect attempt, on whatever state."""
    await device.close()
    await device.close()

    with pytest.raises(DisconnectedError):
        await device.get("/value")


@pytest.mark.asyncio
async def test_a_subclass_reshapes_the_response_without_touching_health():
    """The Eiger case: the envelope is the device's convention, not HTTP's."""

    class Unwrapping(FakeDevice):
        async def get(self, path: str):
            return (await super().get(path))["value"]

    connection = Unwrapping()
    await connection.connect()
    try:
        assert await connection.get("/value") == 1.5
    finally:
        await connection.close()
