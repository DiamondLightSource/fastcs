import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from fastcs.connections import (
    DEFAULT_RECONNECT_ATTEMPTS,
    DEFAULT_RECONNECT_PERIOD,
    Connection,
    ConnectionPolicy,
    DisconnectedError,
    DRAPolicy,
    Supervisor,
)
from fastcs.connections.supervisor import connection_of, supervisor_of
from fastcs.logging import logger


class FakeConnection(Connection):
    """Opens when told to, and answers ``read`` until told to fail."""

    def __init__(self) -> None:
        self.fail_connect: Exception | None = None
        self.failures_left: int | None = None
        """With ``fail_connect``, how many connects fail before one succeeds."""
        self.fail_io: Exception | None = None
        self.connects = 0
        self.closes = 0
        self.reads = 0

    async def connect(self) -> None:
        self.connects += 1
        if self.fail_connect is None:
            return
        if self.failures_left is not None:
            if self.failures_left == 0:
                return
            self.failures_left -= 1
        raise self.fail_connect

    async def close(self) -> None:
        self.closes += 1

    async def read(self) -> int:
        self.reads += 1
        if self.fail_io is not None:
            raise self.fail_io
        return 1

    async def read_twice(self) -> int:
        return await self.read() + await self.read()

    def describe(self) -> str:
        return "a fake"


@pytest.fixture
def log_records() -> Iterator[list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    handler = logger.add(
        lambda message: records.append(
            {"event": message.record["message"], **message.record["extra"]}
        ),
        level="DEBUG",
    )
    yield records
    logger.remove(handler)


async def opened(
    connection: FakeConnection | None = None, **kwargs: Any
) -> Supervisor[FakeConnection]:
    supervisor = Supervisor(connection or FakeConnection(), **kwargs)
    await supervisor.open()
    return supervisor


def fail(supervisor: Supervisor[FakeConnection]) -> None:
    """Drop the link, as a failing read would."""
    supervisor.mark_down(ConnectionResetError())


# The handle


def test_a_handle_passes_as_the_connection_type():
    supervisor = Supervisor(FakeConnection())

    assert isinstance(supervisor.handle, FakeConnection)


def test_a_handle_is_not_the_connection():
    supervisor = Supervisor(FakeConnection())

    assert supervisor.handle is not supervisor.connection


def test_the_connection_behind_a_handle_is_found():
    supervisor = Supervisor(FakeConnection())

    assert connection_of(supervisor.handle) is supervisor.connection


def test_a_bare_connection_is_its_own_connection():
    connection = FakeConnection()

    assert connection_of(connection) is connection


def test_the_supervisor_behind_a_handle_is_found():
    supervisor = Supervisor(FakeConnection())

    assert supervisor_of(supervisor.handle) is supervisor


def test_a_bare_connection_has_no_supervisor():
    assert supervisor_of(FakeConnection()) is None


def test_sync_members_pass_straight_through_a_handle():
    supervisor = Supervisor(FakeConnection())

    assert supervisor.handle.describe() == "a fake"


def test_setting_through_a_handle_sets_on_the_connection():
    supervisor = Supervisor(FakeConnection())

    supervisor.handle.reads = 5

    assert supervisor.connection.reads == 5


@pytest.mark.asyncio
async def test_an_io_method_is_called_through_a_handle():
    supervisor = await opened()

    assert await supervisor.handle.read() == 1


@pytest.mark.asyncio
async def test_nested_calls_are_counted_once():
    """A connection calling its own methods does not pass the handle again."""
    supervisor = await opened(policy=ConnectionPolicy(timeout_count=2))
    supervisor.connection.fail_io = TimeoutError()

    with pytest.raises(TimeoutError):
        await supervisor.handle.read_twice()

    # One timeout counted, not two, so the link is still up
    assert supervisor.up


def test_the_defaults():
    supervisor = Supervisor(FakeConnection())

    assert supervisor.reconnect_attempts == DEFAULT_RECONNECT_ATTEMPTS
    assert supervisor.reconnect_period == DEFAULT_RECONNECT_PERIOD


def test_the_policy_comes_from_the_connection_class():
    class DRAConnection(FakeConnection):
        policy = DRAPolicy()

    assert Supervisor(DRAConnection()).policy == DRAPolicy()


def test_a_policy_given_to_the_supervisor_overrides_the_class():
    policy = ConnectionPolicy(timeout_count=10)

    assert Supervisor(FakeConnection(), policy=policy).policy is policy


def test_a_supervisor_is_named_after_its_connection_by_default():
    assert Supervisor(FakeConnection()).name == "FakeConnection"


# The boundary


@pytest.mark.asyncio
async def test_a_call_before_the_link_is_opened_fails_fast():
    supervisor = Supervisor(FakeConnection())

    with pytest.raises(DisconnectedError):
        await supervisor.handle.read()


@pytest.mark.asyncio
async def test_a_call_while_the_link_is_down_does_no_io():
    supervisor = await opened()
    fail(supervisor)

    with pytest.raises(DisconnectedError):
        await supervisor.handle.read()

    assert supervisor.connection.reads == 0


@pytest.mark.asyncio
async def test_a_link_error_is_raised_as_disconnected():
    supervisor = await opened()
    supervisor.connection.fail_io = ConnectionResetError()

    with pytest.raises(DisconnectedError):
        await supervisor.handle.read()


@pytest.mark.asyncio
async def test_a_link_error_marks_the_link_down():
    supervisor = await opened()
    supervisor.connection.fail_io = ConnectionResetError()

    with pytest.raises(DisconnectedError):
        await supervisor.handle.read()

    assert not supervisor.up


@pytest.mark.asyncio
async def test_a_link_error_is_caught_even_if_the_caller_swallows_it():
    """The supervisor sees the error where it starts, so no getter can hide it."""
    supervisor = await opened()
    supervisor.connection.fail_io = ConnectionResetError()

    try:
        await supervisor.handle.read()
    except Exception:
        pass

    assert not supervisor.up


@pytest.mark.asyncio
async def test_a_device_error_is_passed_back_unchanged():
    supervisor = await opened()
    supervisor.connection.fail_io = ValueError("rejected")

    with pytest.raises(ValueError, match="rejected"):
        await supervisor.handle.read()


@pytest.mark.asyncio
async def test_a_device_error_leaves_the_link_up():
    supervisor = await opened()
    supervisor.connection.fail_io = ValueError("rejected")

    with pytest.raises(ValueError):
        await supervisor.handle.read()

    assert supervisor.up


@pytest.mark.asyncio
async def test_a_driver_may_say_its_device_is_offline():
    supervisor = await opened()
    supervisor.connection.fail_io = DisconnectedError("device reports OFFLINE")

    with pytest.raises(DisconnectedError, match="OFFLINE"):
        await supervisor.handle.read()

    assert not supervisor.up


@pytest.mark.asyncio
async def test_one_timeout_is_passed_back_as_it_is():
    supervisor = await opened()
    supervisor.connection.fail_io = TimeoutError()

    with pytest.raises(TimeoutError):
        await supervisor.handle.read()

    assert supervisor.up


@pytest.mark.asyncio
async def test_timeouts_in_a_row_mark_the_link_down():
    supervisor = await opened(policy=ConnectionPolicy(timeout_count=3))
    supervisor.connection.fail_io = TimeoutError()
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await supervisor.handle.read()

    with pytest.raises(DisconnectedError):
        await supervisor.handle.read()


@pytest.mark.asyncio
async def test_a_good_call_resets_the_timeout_count():
    supervisor = await opened(policy=ConnectionPolicy(timeout_count=2))
    supervisor.connection.fail_io = TimeoutError()
    with pytest.raises(TimeoutError):
        await supervisor.handle.read()
    supervisor.connection.fail_io = None
    await supervisor.handle.read()
    supervisor.connection.fail_io = TimeoutError()

    with pytest.raises(TimeoutError):
        await supervisor.handle.read()

    assert supervisor.up


@pytest.mark.asyncio
async def test_going_down_is_logged_once(log_records):
    supervisor = await opened()
    supervisor.connection.fail_io = ConnectionResetError()

    for _ in range(3):
        with pytest.raises(DisconnectedError):
            await supervisor.handle.read()

    assert [r["event"] for r in log_records].count("Connection down") == 1


# Reconnecting


async def started(
    connection: FakeConnection | None = None, **kwargs: Any
) -> tuple[Supervisor[FakeConnection], list[BaseException]]:
    supervisor = await opened(connection, **kwargs)
    fatal: list[BaseException] = []
    supervisor.start(asyncio.get_event_loop(), on_fatal=fatal.append)
    return supervisor, fatal


async def gave_up(supervisor: Supervisor) -> None:
    async def wait() -> None:
        while not supervisor.gave_up:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_a_link_that_dropped_is_reconnected():
    supervisor, _ = await started(reconnect_period=0.001)
    try:
        fail(supervisor)

        await asyncio.wait_for(supervisor.wait_up(), timeout=2)

        assert supervisor.connection.connects == 2
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_the_link_is_closed_before_it_is_reopened():
    supervisor, _ = await started(reconnect_period=0.001)
    try:
        fail(supervisor)

        await asyncio.wait_for(supervisor.wait_up(), timeout=2)

        assert supervisor.connection.closes == 1
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_failing_reconnect_gives_up_after_its_attempts():
    supervisor, _ = await started(reconnect_period=0.001, reconnect_attempts=3)
    try:
        supervisor.connection.fail_connect = RuntimeError("still down")
        fail(supervisor)

        await gave_up(supervisor)

        # The initial open, then three attempts
        assert supervisor.connection.connects == 4
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_giving_up_is_terminal():
    supervisor, _ = await started(reconnect_period=0.001, reconnect_attempts=3)
    try:
        supervisor.connection.fail_connect = RuntimeError("still down")
        fail(supervisor)
        await gave_up(supervisor)
        connects = supervisor.connection.connects

        await asyncio.sleep(0.05)

        assert supervisor.connection.connects == connects
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_clean_reconnect_restores_the_retry_budget():
    supervisor, _ = await started(reconnect_period=0.001, reconnect_attempts=3)
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        for _ in range(2):
            # Two of the three attempts fail each time, five in all
            supervisor.connection.failures_left = 2
            fail(supervisor)
            await asyncio.wait_for(supervisor.wait_up(), timeout=2)

        assert not supervisor.gave_up
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_terminal_failure_gives_up_without_spending_the_budget():
    supervisor, _ = await started(
        reconnect_period=0.001,
        reconnect_attempts=1000,
        policy=DRAPolicy(fatal=False),
    )
    try:
        supervisor.connection.fail_connect = FileNotFoundError("/dev/ttyACM0")
        fail(supervisor)

        await gave_up(supervisor)

        assert supervisor.connection.connects == 2  # the open, then one attempt
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_terminal_failure_is_logged_with_the_policys_reason(log_records):
    supervisor, _ = await started(reconnect_period=0.001, policy=DRAPolicy())
    try:
        supervisor.connection.fail_connect = FileNotFoundError("/dev/ttyACM0")
        fail(supervisor)

        await gave_up(supervisor)

        giving_up = [r for r in log_records if r["event"] == "Giving up"]
        assert giving_up[0]["reason"] == DRAPolicy().reason(supervisor.connection)
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_terminal_failure_under_a_fatal_policy_is_fatal():
    supervisor, fatal = await started(reconnect_period=0.001, policy=DRAPolicy())
    try:
        error = FileNotFoundError("/dev/ttyACM0")
        supervisor.connection.fail_connect = error
        fail(supervisor)

        await gave_up(supervisor)

        assert fatal == [error]
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_a_terminal_failure_under_a_non_fatal_policy_only_stalls():
    supervisor, fatal = await started(
        reconnect_period=0.001, policy=DRAPolicy(fatal=False)
    )
    try:
        supervisor.connection.fail_connect = FileNotFoundError("/dev/ttyACM0")
        fail(supervisor)

        await gave_up(supervisor)

        assert fatal == []
    finally:
        supervisor.stop()


@pytest.mark.asyncio
async def test_running_out_of_attempts_is_not_fatal():
    supervisor, fatal = await started(
        reconnect_period=0.001, reconnect_attempts=2, policy=DRAPolicy()
    )
    try:
        supervisor.connection.fail_connect = OSError("I/O error")
        fail(supervisor)

        await gave_up(supervisor)

        assert fatal == []
    finally:
        supervisor.stop()


# Dependencies


@pytest.mark.asyncio
async def test_a_dependent_waits_rather_than_spending_its_attempts():
    base, _ = await started(reconnect_period=0.001, reconnect_attempts=1000)
    layered, _ = await started(reconnect_period=0.001, reconnect_attempts=1)
    layered.dependencies = [base]
    try:
        base.connection.fail_connect = RuntimeError("down")
        fail(base)
        fail(layered)
        await asyncio.sleep(0.05)

        # Not attempted at all while its dependency is down
        assert layered.connection.connects == 1

        base.connection.fail_connect = None
        await asyncio.wait_for(layered.wait_up(), timeout=2)
    finally:
        base.stop()
        layered.stop()


@pytest.mark.asyncio
async def test_a_dependent_waits_for_every_dependency():
    """A connection over two links is no more usable with one than with neither."""
    first, _ = await started(reconnect_period=0.001, reconnect_attempts=1000)
    second, _ = await started(reconnect_period=0.001, reconnect_attempts=1000)
    layered, _ = await started(reconnect_period=0.001)
    layered.dependencies = [first, second]
    try:
        for dependency in (first, second):
            dependency.connection.fail_connect = RuntimeError("down")
            fail(dependency)
        fail(layered)
        first.connection.fail_connect = None
        await asyncio.wait_for(first.wait_up(), timeout=2)
        await asyncio.sleep(0.05)

        assert layered.connection.connects == 1
    finally:
        for supervisor in (first, second, layered):
            supervisor.stop()


@pytest.mark.asyncio
async def test_a_dependent_stalls_when_a_dependency_gives_up(log_records):
    healthy, _ = await started(reconnect_period=0.001)
    doomed, _ = await started(reconnect_period=0.001, reconnect_attempts=1)
    layered, _ = await started(reconnect_period=0.001)
    layered.dependencies = [healthy, doomed]
    try:
        doomed.connection.fail_connect = RuntimeError("down for good")
        fail(doomed)
        fail(layered)

        await gave_up(doomed)
        await asyncio.sleep(0.05)

        stalled = [r for r in log_records if r["event"].startswith("Stalled")]
        assert stalled[0]["dependencies"] == [doomed.name]
    finally:
        for supervisor in (healthy, doomed, layered):
            supervisor.stop()


@pytest.mark.asyncio
async def test_a_stalled_dependent_makes_no_attempt():
    doomed, _ = await started(reconnect_period=0.001, reconnect_attempts=1)
    layered, _ = await started(reconnect_period=0.001)
    layered.dependencies = [doomed]
    try:
        doomed.connection.fail_connect = RuntimeError("down for good")
        fail(doomed)
        fail(layered)

        await gave_up(doomed)
        await asyncio.sleep(0.05)

        assert layered.connection.connects == 1
    finally:
        doomed.stop()
        layered.stop()
