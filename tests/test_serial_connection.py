import os
import threading

import pytest
import pytest_asyncio

from fastcs.connections import DisconnectedError, SerialConnection


def _reply_to_r(device: int):
    buffer = b""
    while True:
        try:
            buffer += os.read(device, 1024)
        except OSError:
            return

        while b"\r\n" in buffer:
            line, buffer = buffer.split(b"\r\n", 1)
            if line == b"R?":
                os.write(device, b"5\r\n")


@pytest_asyncio.fixture
async def connection():
    device, port = os.openpty()
    threading.Thread(target=_reply_to_r, args=(device,), daemon=True).start()
    connection = SerialConnection(os.ttyname(port), timeout=0.1)
    await connection.connect()
    yield connection
    await connection.close()
    os.close(port)
    os.close(device)


@pytest.mark.asyncio
async def test_query_adds_and_strips_terminator(connection: SerialConnection):
    assert await connection.query("R?") == "5"


@pytest.mark.asyncio
async def test_timeout_closes_connection(connection: SerialConnection):
    with pytest.raises(TimeoutError):
        await connection.query("NO_REPLY?")

    with pytest.raises(DisconnectedError):
        await connection.write("R=5")


@pytest.mark.asyncio
async def test_write_and_bytes_methods(connection: SerialConnection):
    await connection.write("R=5")
    await connection.write_bytes(b"R=6\r\n")
    assert await connection.query_bytes(b"R?\r\n", 3) == b"5\r\n"
