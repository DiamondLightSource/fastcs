from unittest.mock import AsyncMock, MagicMock

import pytest

from fastcs.connections.ip_connection import DisconnectedError, IPConnection


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


@pytest.mark.asyncio
async def test_a_peer_that_closes_instead_of_answering_marks_the_link_down():
    """``readline`` returns b"" at EOF, which is a dead link, not an empty reply."""
    conn = IPConnection()
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.send_message = AsyncMock()
    mock_stream.receive_response = AsyncMock(return_value="")
    conn._IPConnection__connection = mock_stream  # pyright: ignore[reportAttributeAccessIssue]
    conn._set_connected()

    with pytest.raises(DisconnectedError):
        await conn.send_query("ID?\r\n")

    # Without this the caller just gets "", fails to parse it, and retries forever
    # while the reconnect task stays idle.
    assert not conn.connected


@pytest.mark.asyncio
async def test_a_real_response_leaves_the_link_up():
    conn = IPConnection()
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_stream.send_message = AsyncMock()
    mock_stream.receive_response = AsyncMock(return_value="ID=1\r\n")
    conn._IPConnection__connection = mock_stream  # pyright: ignore[reportAttributeAccessIssue]
    conn._set_connected()

    assert await conn.send_query("ID?\r\n") == "ID=1\r\n"
    assert conn.connected
