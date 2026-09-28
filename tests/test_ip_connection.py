from unittest.mock import AsyncMock, MagicMock

import pytest

from fastcs.connections import DisconnectedError, IPConnection, Supervisor


@pytest.fixture
def connection():
    conn = IPConnection()
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.close = AsyncMock()
    conn._IPConnection__connection = mock_stream  # pyright: ignore[reportAttributeAccessIssue]
    return conn, mock_stream


@pytest.mark.asyncio
async def test_close_when_not_connected(connection):
    conn, mock_stream = connection
    conn._IPConnection__connection = None

    await conn.close()

    mock_stream.close.assert_not_awaited()
    assert conn._IPConnection__connection is None


@pytest.mark.asyncio
async def test_close_connected_and_connection_reset(connection):
    conn, mock_stream = connection

    await conn.close()
    mock_stream.close.assert_awaited_once()
    assert conn._IPConnection__connection is None

    conn._IPConnection__connection = mock_stream
    mock_stream.close.side_effect = ConnectionResetError

    await conn.close()
    assert mock_stream.close.await_count == 2
    assert conn._IPConnection__connection is None

    # Other exceptions are propagated, but connection is reset
    conn._IPConnection__connection = mock_stream
    mock_stream.close.side_effect = OSError

    with pytest.raises(OSError):
        await conn.close()

    assert conn._IPConnection__connection is None


def _answering(response: str) -> IPConnection:
    conn = IPConnection()
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.send_message = AsyncMock()
    mock_stream.receive_response = AsyncMock(return_value=response)
    conn._IPConnection__connection = mock_stream  # pyright: ignore[reportAttributeAccessIssue]
    # Already open, as the mock stream is
    conn.connect = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_a_peer_that_closes_instead_of_answering_marks_the_link_down():
    """``readline`` returns b"" at EOF, which is a dead link, not an empty reply."""
    supervisor = Supervisor(_answering(""))
    await supervisor.open()

    with pytest.raises(DisconnectedError):
        await supervisor.handle.send_query("ID?\r\n")

    # Without this the caller just gets "", fails to parse it, and retries forever
    # while the reconnect loop stays idle.
    assert not supervisor.up


@pytest.mark.asyncio
async def test_a_real_response_leaves_the_link_up():
    supervisor = Supervisor(_answering("ID=1\r\n"))
    await supervisor.open()

    assert await supervisor.handle.send_query("ID?\r\n") == "ID=1\r\n"
    assert supervisor.up
