import asyncio

import pytest
import pytest_asyncio

from fastcs.connections import DisconnectedError, IPConnection


async def _reply_to_r(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    while line := await reader.readuntil(b"\r\n"):
        if line == b"R?\r\n":
            writer.write(b"5\r\n")
        elif line == b"LONG?\r\n":
            writer.write(b"12345\r\n")
        await writer.drain()


@pytest_asyncio.fixture
async def connection():
    server = await asyncio.start_server(_reply_to_r, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    connection = IPConnection("127.0.0.1", port, timeout=0.1)
    await connection.connect()
    yield connection
    await connection.close()
    server.close()


@pytest.mark.asyncio
async def test_query_adds_and_strips_terminator(connection: IPConnection):
    assert await connection.query("R?") == "5"


@pytest.mark.asyncio
async def test_connect_when_connected_raises(connection: IPConnection):
    with pytest.raises(RuntimeError, match="already connected"):
        await connection.connect()

    assert await connection.query("R?") == "5"


@pytest.mark.asyncio
async def test_timeout_closes_connection(connection: IPConnection):
    with pytest.raises(TimeoutError):
        await connection.query("NO_REPLY?")

    with pytest.raises(DisconnectedError):
        await connection.write("R=5")


@pytest.mark.asyncio
async def test_write_and_bytes_methods(connection: IPConnection):
    await connection.write("R=5")
    await connection.write_bytes(b"R=6\r\n")
    assert await connection.query_bytes(b"R?\r\n", 3) == b"5\r\n"


@pytest.mark.asyncio
async def test_device_hanging_up_raises_connection_error():
    async def hang_up(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n")
        writer.write(b"partial")
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    connection = IPConnection("127.0.0.1", port, timeout=0.1)
    await connection.connect()

    with pytest.raises(ConnectionError, match="closed the link"):
        await connection.query("R?")
    with pytest.raises(DisconnectedError):
        await connection.write("R=5")

    server.close()


@pytest.mark.asyncio
async def test_reply_longer_than_limit_raises_and_closes_connection():
    server = await asyncio.start_server(_reply_to_r, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    connection = IPConnection("127.0.0.1", port, timeout=0.1, limit=4)
    await connection.connect()

    with pytest.raises(ValueError, match="longer than 4 bytes"):
        await connection.query("LONG?")
    with pytest.raises(DisconnectedError):
        await connection.write("R=5")

    server.close()
