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
    async def send(self) -> None:
        raise ConnectionResetError()


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

    with pytest.raises(ConnectionResetError):
        await connection.get(ConnectionResetError())
    assert not connection.state.up


@pytest.mark.asyncio
async def test_bare_decorator_and_inert_state():
    connection = _FlakyConnection()

    with pytest.raises(ConnectionResetError):
        await connection.send()
    assert connection.state.up
