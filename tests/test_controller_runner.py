import asyncio
import sys

import pytest

from fastcs.attributes import AttrR, Polled
from fastcs.connections import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RECONNECT_PERIOD,
    Connection,
    Connections,
)
from fastcs.controllers import Controller, ControllerRunner
from fastcs.controllers.runner import MAX_BUILD_PASSES, IntrospectionMismatchError
from fastcs.methods import scan
from fastcs.util import ONCE


class FakeConnection(Connection[str]):
    """A connection that opens when told to, and records what was asked of it."""

    def __init__(self, introspection: str = "v1", **kwargs) -> None:
        super().__init__(**kwargs)
        self.introspection = introspection
        self.fail_next: Exception | None = None
        self.connects = 0
        self.closes = 0

    async def connect(self) -> str:
        self.connects += 1
        if self.fail_next is not None:
            raise self.fail_next
        return self.introspection

    async def close(self) -> None:
        self.closes += 1


class LifecycleController(Controller):
    """Records every lifecycle hook the runner is supposed to call."""

    def __init__(self, connection: Connection | None = None):
        self.connection = connection
        super().__init__()
        self.events: list[str] = []
        self.count = AttrR(int)

    async def build(self):
        self.events.append("build")

    async def setup(self):
        self.events.append("setup")

    @scan(ONCE)
    async def read_once(self):
        self.events.append("initial")
        await self.count.update(self.count.readback + 1)


@pytest.mark.asyncio
async def test_the_runner_drives_the_whole_lifecycle():
    connection = FakeConnection()
    controller = LifecycleController(connection)
    runner = ControllerRunner(controller)

    await runner.start()
    try:
        assert controller.events == ["build", "setup", "initial"]
        assert controller.count.readback == 1
        assert connection.connects == 1
        assert connection.connected
    finally:
        await runner.stop()

    # Shutdown is a runner operation, not an author hook
    assert connection.closes == 1


@pytest.mark.asyncio
async def test_connections_open_before_anything_is_built():
    """A ``build`` runs against an open link, so it can ask the device questions."""
    connection = FakeConnection()
    order: list[str] = []

    class RecordingController(Controller):
        def __init__(self):
            self.connection = connection
            super().__init__()

        async def build(self):
            order.append(f"build(connected={connection.connected})")

    runner = ControllerRunner(RecordingController())
    await runner.build()

    assert order == ["build(connected=True)"]


@pytest.mark.asyncio
async def test_build_builds_the_apis_before_anything_is_set_up():
    """A transport is wired to the APIs between build and start."""
    controller = LifecycleController(FakeConnection())
    runner = ControllerRunner(controller)

    apis = await runner.build()

    assert [api.path for api in apis] == [[]]
    assert "count" in apis[0].attributes
    assert controller.events == ["build"]
    assert runner.controller_apis == apis


@pytest.mark.asyncio
async def test_start_builds_when_build_has_not_run():
    runner = ControllerRunner(LifecycleController(FakeConnection()))

    await runner.start()
    try:
        assert len(runner.controller_apis) == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_runner_takes_several_controllers():
    controllers = [
        LifecycleController(FakeConnection()),
        LifecycleController(FakeConnection()),
    ]
    runner = ControllerRunner(controllers)

    await runner.start()
    try:
        assert len(runner.controller_apis) == 2
        assert all(controller.count.readback == 1 for controller in controllers)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_controller_with_no_connection_still_runs():
    """A soft controller that groups others has nothing to connect."""
    controller = LifecycleController(None)
    runner = ControllerRunner(controller)

    await runner.start()
    try:
        assert runner.connections == []
        assert controller.events == ["build", "setup", "initial"]
        assert controller.connected
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_setup_runs_once_the_whole_tree_is_built():
    """A parent's ``setup`` can read a child that only exists after ``build``."""
    order: list[str] = []

    class Child(Controller):
        async def build(self):
            order.append("child build")

        async def setup(self):
            order.append("child setup")

    class Parent(Controller):
        async def build(self):
            order.append("parent build")
            self.add_sub_controller("CHILD", Child())

        async def setup(self):
            order.append("parent setup")

    runner = ControllerRunner(Parent())
    await runner.start()
    try:
        assert order == ["parent build", "child build", "parent setup", "child setup"]
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_build_repeats_until_the_tree_stops_growing():
    class Tier(Controller):
        def __init__(self, depth: int) -> None:
            super().__init__()
            self._depth = depth

        async def build(self):
            if self._depth:
                self.add_sub_controller("SUB", Tier(self._depth - 1))

    runner = ControllerRunner(Tier(3))
    await runner.build()

    controller = runner._controllers[0]
    for _ in range(3):
        controller = controller.sub_controllers["SUB"]  # type: ignore[assignment]
    assert controller.sub_controllers == {}


@pytest.mark.asyncio
async def test_a_tree_that_never_settles_is_caught():
    class Runaway(Controller):
        async def build(self):
            self.add_sub_controller("SUB", Runaway())

    runner = ControllerRunner(Runaway())

    with pytest.raises(RuntimeError, match=f"{MAX_BUILD_PASSES} build passes"):
        await runner.build()


@pytest.mark.asyncio
async def test_build_receives_the_connections_introspection():
    received: list[object] = []

    class IntrospectingController(Controller):
        def __init__(self):
            self.connection = FakeConnection("api-1.8.0")
            super().__init__()

        async def build(self, info: str) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
            received.append(info)

    await ControllerRunner(IntrospectingController()).build()

    assert received == ["api-1.8.0"]


@pytest.mark.asyncio
async def test_asking_for_introspection_without_a_connection_is_an_error():
    class Confused(Controller):
        async def build(self, info) -> None: ...  # pyright: ignore[reportIncompatibleMethodOverride]

    with pytest.raises(TypeError, match="no connection"):
        await ControllerRunner(Confused()).build()


@pytest.mark.asyncio
async def test_controllers_sharing_a_connection_are_one_connection():
    """Identity, not equality: the tree is not the unit of failure, the link is."""
    connection = FakeConnection()

    class Parent(Controller):
        def __init__(self):
            self.connection = connection
            super().__init__()
            self.add_sub_controller("A", LifecycleController(connection))
            self.add_sub_controller("B", LifecycleController(connection))

    runner = ControllerRunner(Parent())
    await runner.build()

    assert runner.connections == [connection]
    assert connection.connects == 1


@pytest.mark.asyncio
async def test_a_connection_created_during_build_is_rejected():
    """It would never be opened, and so never reconnected either."""

    class LateConnector(Controller):
        async def build(self):
            child = LifecycleController(FakeConnection())
            self.add_sub_controller("LATE", child)

    with pytest.raises(RuntimeError, match="did not open"):
        await ControllerRunner(LateConnector()).build()


@pytest.mark.asyncio
async def test_a_declared_but_unclaimed_connection_is_warned_about(monkeypatch):
    warnings: list[dict] = []
    monkeypatch.setattr(
        "fastcs.controllers.runner.logger.warning",
        lambda event, **kwargs: warnings.append({"event": event, **kwargs}),
    )

    claimed = FakeConnection()
    connections = Connections({"used": claimed, "spare": FakeConnection()})
    connections.get("used", FakeConnection)

    runner = ControllerRunner(LifecycleController(claimed), connections=connections)
    await runner.start()
    try:
        assert [w["connection"] for w in warnings if "never used" in w["event"]] == [
            "spare"
        ]
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_connection_nothing_polls_is_warned_about(monkeypatch):
    warnings: list[dict] = []
    monkeypatch.setattr(
        "fastcs.controllers.runner.logger.warning",
        lambda event, **kwargs: warnings.append({"event": event, **kwargs}),
    )

    class OnDemandOnly(Controller):
        def __init__(self, connection):
            self.connection = connection
            super().__init__()

    runner = ControllerRunner(OnDemandOnly(FakeConnection()))
    await runner.start()
    try:
        assert any("no polled attribute" in w["event"] for w in warnings)
    finally:
        await runner.stop()

    warnings.clear()

    class Polling(Controller):
        def __init__(self, connection):
            self.connection = connection
            super().__init__()
            self.value = AttrR(int, getter=Polled(self._get, period=0.2))

        async def _get(self) -> int:
            return 1

    runner = ControllerRunner(Polling(FakeConnection()))
    await runner.start()
    try:
        assert not any("no polled attribute" in w["event"] for w in warnings)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_the_runner_reconnects_a_connection_that_dropped_out():
    """The connection's own IO marks it down; one task per connection brings it back."""
    connection = FakeConnection(reconnect_period=0.01)
    runner = ControllerRunner(LifecycleController(connection))
    await runner.start()
    try:
        assert connection.connected

        # What a connection's IO does when its transport fails
        connection.set_disconnected()
        await connection.wait_up()

        assert connection.connected
        assert connection.connects == 2
        # Closed before the reconnect attempt, tolerating an already-closed link
        assert connection.closes == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_failing_reconnect_keeps_trying_then_gives_up():
    connection = FakeConnection(reconnect_period=0.001, max_attempts=3)
    runner = ControllerRunner(LifecycleController(connection))
    await runner.start()
    try:
        connection.fail_next = RuntimeError("still down")
        connection.set_disconnected()

        state = runner._state[connection]
        await asyncio.wait_for(state.exhausted.wait(), timeout=2)

        assert state.attempts == 3
        assert not connection.connected

        # Terminal until the process restarts: no further attempts
        attempts_at_exhaustion = connection.connects
        await asyncio.sleep(0.05)
        assert connection.connects == attempts_at_exhaustion
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_clean_reconnect_restores_the_retry_budget():
    connection = FakeConnection(reconnect_period=0.001, max_attempts=1000)
    runner = ControllerRunner(LifecycleController(connection))
    await runner.start()
    try:
        connection.fail_next = RuntimeError("down")
        connection.set_disconnected()
        await asyncio.sleep(0.02)
        assert runner._state[connection].attempts > 0

        connection.fail_next = None
        await asyncio.wait_for(connection.wait_up(), timeout=2)

        assert runner._state[connection].attempts == 0
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_dependent_waits_rather_than_spending_its_budget():
    """No attempt means no increment, so the budget freezes while waiting."""
    base = FakeConnection(reconnect_period=0.001, max_attempts=1000)
    layered = FakeConnection(depends_on=base, reconnect_period=0.001)

    runner = ControllerRunner([LifecycleController(base), LifecycleController(layered)])
    await runner.start()
    try:
        base.fail_next = RuntimeError("down")
        base.set_disconnected()
        layered.set_disconnected()

        await asyncio.sleep(0.05)

        # The dependent has not attempted at all while its dependency is down
        assert runner._state[layered].attempts == 0
        assert layered.connects == 1

        base.fail_next = None
        await asyncio.wait_for(layered.wait_up(), timeout=2)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_dependent_is_released_when_its_dependency_gives_up(monkeypatch):
    """Released rather than left hanging, so it stalls loudly."""
    errors: list[dict] = []
    monkeypatch.setattr(
        "fastcs.controllers.runner.logger.error",
        lambda event, **kwargs: errors.append({"event": event, **kwargs}),
    )

    base = FakeConnection(reconnect_period=0.001, max_attempts=1)
    layered = FakeConnection(depends_on=base, reconnect_period=0.001)

    runner = ControllerRunner(
        [LifecycleController(base), LifecycleController(layered)],
        connections=Connections({"base": base, "layered": layered}),
    )
    await runner.start()
    try:
        base.fail_next = RuntimeError("down for good")
        base.set_disconnected()
        layered.set_disconnected()

        await asyncio.sleep(0.2)

        gave_up = [e for e in errors if e["event"] == "Giving up"]
        assert gave_up and gave_up[0]["connection"] == "base"
        # The give-up message names what it takes down with it
        assert gave_up[0]["blocks"] == ["layered"]

        stalled = [e for e in errors if e["event"].startswith("Stalled")]
        assert stalled and stalled[0]["connection"] == "layered"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_dependency_cycle_is_caught_at_startup():
    first = FakeConnection()
    second = FakeConnection(depends_on=first)
    first.depends_on = second

    runner = ControllerRunner([LifecycleController(first), LifecycleController(second)])

    with pytest.raises(ValueError, match="Cycle in connection dependencies"):
        await runner.build()


@pytest.mark.asyncio
async def test_a_device_that_comes_back_different_is_fatal():
    connection = FakeConnection("v1", reconnect_period=0.001)
    runner = ControllerRunner(LifecycleController(connection))
    await runner.start()
    try:
        connection.introspection = "v2"
        connection.set_disconnected()

        await asyncio.wait_for(runner.fatal_error.wait(), timeout=2)

        assert isinstance(runner.fatal_reason, IntrospectionMismatchError)
        assert "v1" in str(runner.fatal_reason)
        assert "v2" in str(runner.fatal_reason)
        # Not marked back up against a tree that no longer matches the hardware
        assert not connection.connected
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_an_uncomparable_introspection_result_says_so():
    class Ambiguous:
        def __ne__(self, other):
            raise ValueError("truth value of an array is ambiguous")

    connection = FakeConnection(Ambiguous(), reconnect_period=0.001)  # type: ignore[arg-type]
    runner = ControllerRunner(LifecycleController(connection))
    await runner.build()

    with pytest.raises(TypeError, match="did not give a single bool"):
        await runner._attempt(connection)


@pytest.mark.asyncio
async def test_scans_are_gated_on_the_connection():
    connection = FakeConnection(reconnect_period=0.001)

    class Scanning(Controller):
        def __init__(self):
            self.connection = connection
            super().__init__()
            self.scans = 0

        @scan(0.001)
        async def tick(self):
            self.scans += 1

    controller = Scanning()
    runner = ControllerRunner(controller)
    await runner.start()
    try:
        await asyncio.sleep(0.02)
        assert controller.scans > 0

        connection.fail_next = RuntimeError("down")
        connection.set_disconnected()
        await asyncio.sleep(0.02)

        paused_at = controller.scans
        await asyncio.sleep(0.02)
        assert controller.scans == paused_at
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_stop_closes_connections_in_reverse_declaration_order():
    closed: list[str] = []

    class Recording(FakeConnection):
        def __init__(self, name: str, **kwargs):
            super().__init__(**kwargs)
            self.name = name

        async def close(self) -> None:
            await super().close()
            closed.append(self.name)

    base = Recording("base")
    layered = Recording("layered", depends_on=base)

    runner = ControllerRunner([LifecycleController(base), LifecycleController(layered)])
    await runner.start()
    await runner.stop()

    assert closed == ["layered", "base"]


@pytest.mark.asyncio
async def test_stop_reports_a_failing_close_without_raising(monkeypatch):
    class UncloseableConnection(FakeConnection):
        async def close(self) -> None:
            raise RuntimeError("no")

    logged: list[tuple[str, BaseException | None]] = []

    def record_exception(event, **kwargs):
        # ``logger.exception`` is called from the ``except`` block, so the
        # exception it is reporting is the one currently being handled.
        logged.append((event, sys.exc_info()[1]))

    monkeypatch.setattr("fastcs.controllers.runner.logger.exception", record_exception)

    runner = ControllerRunner(LifecycleController(UncloseableConnection()))
    await runner.start()

    await runner.stop()

    assert len(logged) == 1
    event, error = logged[0]
    assert event == "Exception while closing connection"
    assert isinstance(error, RuntimeError)
    assert str(error) == "no"


@pytest.mark.asyncio
async def test_stop_cancels_the_tasks():
    controller = LifecycleController(FakeConnection())
    runner = ControllerRunner(controller)
    await runner.start()
    tasks = set(runner._tasks)
    assert tasks

    await runner.stop()
    await asyncio.sleep(0)

    assert all(task.cancelled() or task.done() for task in tasks)
    assert not runner._tasks


def test_the_framework_connection_defaults():
    assert DEFAULT_RECONNECT_PERIOD == 1.0
    assert DEFAULT_MAX_ATTEMPTS == 10

    connection = FakeConnection()
    assert connection.reconnect_period == DEFAULT_RECONNECT_PERIOD
    assert connection.max_attempts == DEFAULT_MAX_ATTEMPTS


def test_a_class_default_sits_between_the_framework_and_the_constructor():
    class Patient(FakeConnection):
        reconnect_period = 5.0
        max_attempts = 60

    assert Patient().reconnect_period == 5.0
    assert Patient().max_attempts == 60
    assert Patient(reconnect_period=0.5).reconnect_period == 0.5
    assert Patient(max_attempts=2).max_attempts == 2
