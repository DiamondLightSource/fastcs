from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import zmq
import zmq.asyncio

from fastcs.connections.zeromq_connection import (
    ZeroMQConnection,
    ZeroMQConnectionSettings,
)


@pytest.fixture
def zmq_conn_settings():
    return ZeroMQConnectionSettings()


@pytest.fixture
def zmq_connection(zmq_conn_settings):
    def _factory(type=zmq.SUB, identity=None):
        mock_context = MagicMock()
        with patch.object(zmq.asyncio.Context, "instance", return_value=mock_context):
            mock_socket = MagicMock()
            mock_context.socket.return_value = mock_socket
            zmq_conn_settings.type = type
            zmq_conn_settings.identity = identity
            conn = ZeroMQConnection(zmq_conn_settings)
            conn._socket = mock_socket
        return conn

    return _factory


@pytest.fixture
def sub_connection(zmq_connection):
    return zmq_connection(zmq.SUB)


@pytest.fixture
def dealer_connection(zmq_connection):
    def _factory(identity=None):
        return zmq_connection(zmq.DEALER, identity=identity)

    return _factory


def test_sub_connection(sub_connection):
    assert sub_connection._channel_type == zmq.SUB
    assert sub_connection._endpoint == "tcp://127.0.0.1:1515"


def test_dealer_connection(dealer_connection):
    dealer = dealer_connection()
    assert dealer._channel_type == zmq.DEALER
    assert dealer._endpoint == "tcp://127.0.0.1:1515"


def test_full_url(sub_connection):
    assert sub_connection.full_url("resource") == "tcp://127.0.0.1:1515/resource"


def test_close(sub_connection):
    assert sub_connection._socket is not None
    sub_connection.close()
    assert sub_connection._socket is None


def test_bind_calls_socket_bind(sub_connection):
    sub_connection.bind()
    sub_connection._socket.bind.assert_called_once_with("tcp://127.0.0.1:1515")


def test_connect_calls_socket_connect(sub_connection):
    sub_connection.connect()
    sub_connection._socket.connect.assert_called_once_with("tcp://127.0.0.1:1515")


@pytest.mark.asyncio
async def test_send_returns_zero_on_success(sub_connection):
    sub_connection._socket.send = AsyncMock()
    result = await sub_connection.send(b"hello")
    sub_connection._socket.send.assert_awaited_once_with(b"hello", flags=zmq.NOBLOCK)
    assert result == 0


@pytest.mark.asyncio
async def test_send_returns_minus_one_on_zmq_error(sub_connection):
    sub_connection._socket.send = AsyncMock(side_effect=zmq.ZMQError())
    result = await sub_connection.send(b"hello")
    assert result == -1


@pytest.mark.asyncio
async def test_send_multi_returns_zero_on_success(sub_connection):
    sub_connection._socket.send_multipart = AsyncMock()
    result = await sub_connection.send_multi([b"a", b"b"])
    sub_connection._socket.send_multipart.assert_awaited_once()
    assert result == 0


@pytest.mark.asyncio
async def test_send_multi_returns_minus_one_on_zmq_error(sub_connection):
    sub_connection._socket.send_multipart = AsyncMock(side_effect=zmq.ZMQError())
    result = await sub_connection.send_multi([b"a", b"b"])
    assert result == -1


@pytest.mark.asyncio
async def test_recv_returns_socket_message(sub_connection):
    sub_connection._socket.recv = AsyncMock(return_value=b"response")
    result = await sub_connection.recv()
    sub_connection._socket.recv.assert_awaited_once()
    assert result == b"response"


@pytest.mark.asyncio
async def test_recv_multi_returns_socket_message(sub_connection):
    sub_connection._socket.recv_multipart = AsyncMock(return_value=[b"part1", b"part2"])
    result = await sub_connection.recv_multi()
    sub_connection._socket.recv_multipart.assert_awaited_once()
    assert result == [b"part1", b"part2"]


@pytest.mark.asyncio
async def test_poll_returns_socket_events(sub_connection):
    sub_connection._socket.poll = AsyncMock(return_value=1)
    result = await sub_connection.poll()
    sub_connection._socket.poll.assert_awaited_once_with(-1)
    assert result == 1


@pytest.mark.asyncio
async def test_poll_with_timeout(sub_connection):
    sub_connection._socket.poll = AsyncMock(return_value=0)
    result = await sub_connection.poll(timeout=500)
    sub_connection._socket.poll.assert_awaited_once_with(500)
    assert result == 0


def test_close_after_close_raises():
    settings = ZeroMQConnectionSettings()
    conn = ZeroMQConnection(settings)
    conn.close()
    with pytest.raises(RuntimeError, match="Socket not connected"):
        conn._ensure_socket()


@pytest.mark.asyncio
async def test_send_on_closed_socket_raises(sub_connection):
    sub_connection.close()
    with pytest.raises(RuntimeError, match="Socket not connected"):
        await sub_connection.send(b"hello")


@pytest.mark.asyncio
async def test_recv_on_closed_socket_raises(sub_connection):
    sub_connection.close()
    with pytest.raises(RuntimeError, match="Socket not connected"):
        await sub_connection.recv()


def test_dealer_connection_sets_identity_from_settings(dealer_connection):
    conn = dealer_connection("Hello World!")
    assert conn._identity == "Hello World!"
    conn._socket.setsockopt.assert_called_once_with(zmq.IDENTITY, b"Hello World!")


def test_dealer_connection_generates_random_identity(dealer_connection):
    with patch(
        "fastcs.connections.zeromq_connection.random.randrange",
        side_effect=[0x1234, 0xABCD],
    ):
        conn = dealer_connection()
    assert conn._identity == "1234-abcd"
    conn._socket.setsockopt.assert_called_once_with(zmq.IDENTITY, b"1234-abcd")
