import asyncio

import pytest
import pytest_asyncio

from fastcs.connections import DisconnectedError, IPConnection


async def _reply_to_r(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    while line := await reader.readuntil(b"\r\n"):
        if line == b"R?\r\n":
            writer.write(b"5\r\n")
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
async def test_timeout_closes_connection(connection: IPConnection):
    with pytest.raises(TimeoutError):
        await connection.query("NO_REPLY?")

    with pytest.raises(DisconnectedError):
        await connection.write("R=5")
