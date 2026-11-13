import os
import threading
import time

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
            elif line == b"LONG?":
                os.write(device, b"12345\r\n")


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
async def test_connect_when_connected_raises(connection: SerialConnection):
    with pytest.raises(RuntimeError, match="already connected"):
        await connection.connect()

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


@pytest.mark.asyncio
async def test_device_hanging_up_raises_connection_error():
    def hang_up(device: int):
        os.read(device, 1024)
        os.write(device, b"partial")
        time.sleep(0.05)
        os.close(device)

    device, port = os.openpty()
    threading.Thread(target=hang_up, args=(device,), daemon=True).start()
    connection = SerialConnection(os.ttyname(port), timeout=1)
    await connection.connect()

    with pytest.raises(ConnectionError, match="failed"):
        await connection.query("R?")
    with pytest.raises(DisconnectedError):
        await connection.write("R=5")

    os.close(port)


@pytest.mark.asyncio
async def test_reply_longer_than_limit_raises_and_closes_connection():
    device, port = os.openpty()
    threading.Thread(target=_reply_to_r, args=(device,), daemon=True).start()
    connection = SerialConnection(os.ttyname(port), timeout=0.1, limit=4)
    await connection.connect()

    with pytest.raises(ValueError, match="longer than 4 bytes"):
        await connection.query("LONG?")
    with pytest.raises(DisconnectedError):
        await connection.write("R=5")

    os.close(port)
    os.close(device)


def test_empty_terminator_raises():
    with pytest.raises(ValueError, match="terminator must not be empty"):
        SerialConnection("/dev/null", terminator="")
