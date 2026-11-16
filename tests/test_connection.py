import pytest

from fastcs.connections import (
    Connection,
    ConnectionState,
    DisconnectedError,
    disconnect_on,
)


class _FlakyConnection(Connection):
    def __init__(self) -> None:
        self.calls = 0

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @disconnect_on(TimeoutError)
    async def get(self, error: Exception | None = None) -> int:
        self.calls += 1
        if error is not None:
            raise error
        return self.calls

    @disconnect_on
    async def send(self, error: Exception) -> None:
        raise error


@pytest.mark.asyncio
async def test_listed_error_marks_down_and_gates_later_calls():
    connection = _FlakyConnection()
    connection.state = ConnectionState()

    with pytest.raises(ValueError):
        await connection.get(ValueError())
    assert connection.state.up

    with pytest.raises(TimeoutError):
        await connection.get(TimeoutError())
    assert not connection.state.up

    with pytest.raises(DisconnectedError):
        await connection.get()
    assert connection.calls == 2

    connection.state.mark_up()
    assert await connection.get() == 3

    # Not listed, so it isn't a link error, though it is a ConnectionError
    with pytest.raises(ConnectionResetError):
        await connection.get(ConnectionResetError())
    assert connection.state.up


@pytest.mark.asyncio
async def test_bare_decorator_only_marks_down_on_disconnected_error():
    connection = _FlakyConnection()
    with pytest.raises(DisconnectedError):
        await connection.send(DisconnectedError())
    assert connection.state.up  # The inert state of an unopened connection

    connection.state = ConnectionState()
    for error in (ConnectionResetError(), TimeoutError()):
        with pytest.raises(type(error)):
            await connection.send(error)
    assert connection.state.up

    with pytest.raises(DisconnectedError):
        await connection.send(DisconnectedError())
    assert not connection.state.up


def test_reconnect_attempts_without_period_raises():
    with pytest.raises(TypeError, match="sets reconnect_attempts but not"):

        class _NoPeriodConnection(_FlakyConnection):
            reconnect_attempts = 3


class _SlowConnection(_FlakyConnection):
    disconnect_after_timeouts = 2

    @disconnect_on(OSError)
    async def read(self, error: Exception | None = None) -> None:
        if error is not None:
            raise error


@pytest.mark.asyncio
async def test_timeouts_mark_down_after_disconnect_after_timeouts_in_a_row():
    connection = _SlowConnection()
    connection.state = ConnectionState()

    # OSError includes TimeoutError, but timeouts are counted instead
    with pytest.raises(TimeoutError):
        await connection.read(TimeoutError())
    assert connection.state.up

    await connection.read()  # Starts the count again
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await connection.read(TimeoutError())
    assert not connection.state.up


def test_disconnect_after_timeouts_must_be_at_least_one():
    with pytest.raises(TypeError, match="must be an integer of at least 1"):

        class _NeverConnection(_FlakyConnection):
            disconnect_after_timeouts = 0
