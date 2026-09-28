import asyncio
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from fastcs.attributes import AttrR, AttrRW, Polled, Severity
from fastcs.connections import (
    Connection,
    DisconnectedError,
    DRAPolicy,
    Supervisor,
)
from fastcs.controllers import Controller, ControllerRunner
from fastcs.controllers.runner import CONNECTED_ATTRIBUTE, MAX_BUILD_PASSES
from fastcs.logging import logger
from fastcs.methods import scan
from fastcs.util import ONCE


class FakeConnection(Connection):
    """A connection that opens when told to, and records what was asked of it."""

    def __init__(self) -> None:
        self.fail_connect: Exception | None = None
        self.fail_io: Exception | None = None
        self.connects = 0
        self.closes = 0
        self.value = 1

    async def connect(self) -> None:
        self.connects += 1
        if self.fail_connect is not None:
            raise self.fail_connect

    async def close(self) -> None:
        self.closes += 1

    async def read(self) -> int:
        if self.fail_io is not None:
            raise self.fail_io
        return self.value

    async def write(self, value: int) -> None:
        if self.fail_io is not None:
            raise self.fail_io
        self.value = value


class BaseLink(FakeConnection):
    pass


class LayeredLink(FakeConnection):
    depends_on = [BaseLink]


class LifecycleController(Controller):
    """Records every lifecycle hook the runner is supposed to call."""

    def __init__(self, connection: FakeConnection | None = None):
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


class Polling(Controller):
    """Polls its connection quickly, so it notices and recovers from failures."""

    def __init__(self, connection: FakeConnection | None):
        self.connection = connection
        super().__init__()
        self.value = AttrRW(
            int, getter=Polled(self._read, period=0.001), setter=self._write
        )
        self.polls = 0

    async def _read(self) -> int:
        self.polls += 1
        return await self.connection.read()

    async def _write(self, value: int) -> None:
        await self.connection.write(value)


def supervise(
    connection: FakeConnection, **kwargs: Any
) -> tuple[Supervisor[FakeConnection], FakeConnection]:
    """A supervisor, and the handle a controller would be given."""
    supervisor = Supervisor(connection, name=type(connection).__name__, **kwargs)
    return supervisor, supervisor.handle


def link_down(supervisor: Supervisor) -> None:
    supervisor.mark_down(ConnectionResetError())


async def eventually(predicate, timeout: float = 2) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=timeout)


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


# The lifecycle


@pytest.mark.asyncio
async def test_the_runner_drives_the_whole_lifecycle():
    supervisor, handle = supervise(FakeConnection())
    controller = LifecycleController(handle)
    runner = ControllerRunner(controller, [supervisor])

    await runner.start()
    await runner.stop()

    assert controller.events == ["build", "setup", "initial"]


@pytest.mark.asyncio
async def test_start_opens_the_connection():
    supervisor, handle = supervise(FakeConnection())
    runner = ControllerRunner(LifecycleController(handle), [supervisor])

    await runner.start()
    try:
        assert supervisor.up
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_stop_closes_the_connection():
    """Shutdown is a runner operation, not an author hook."""
    supervisor, handle = supervise(FakeConnection())
    runner = ControllerRunner(LifecycleController(handle), [supervisor])
    await runner.start()

    await runner.stop()

    assert supervisor.connection.closes == 1


@pytest.mark.asyncio
async def test_connections_open_before_anything_is_built():
    """A ``build`` runs against an open link, so it can ask the device questions."""
    supervisor, handle = supervise(FakeConnection())
    read_in_build: list[int] = []

    class Asking(Controller):
        def __init__(self, connection: FakeConnection):
            self.connection = connection
            super().__init__()

        async def build(self):
            read_in_build.append(await self.connection.read())

    runner = ControllerRunner(Asking(handle), [supervisor])
    await runner.build()

    assert read_in_build == [1]


@pytest.mark.asyncio
async def test_build_builds_the_apis_before_anything_is_set_up():
    """A transport is wired to the APIs between build and start."""
    controller = LifecycleController()
    runner = ControllerRunner(controller)

    apis = await runner.build()

    assert "count" in apis[0].attributes
    assert controller.events == ["build"]


@pytest.mark.asyncio
async def test_start_builds_when_build_has_not_run():
    runner = ControllerRunner(LifecycleController())

    await runner.start()
    try:
        assert len(runner.controller_apis) == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_runner_takes_several_controllers():
    controllers = [LifecycleController(), LifecycleController()]
    runner = ControllerRunner(controllers)

    await runner.start()
    try:
        assert all(controller.count.readback == 1 for controller in controllers)
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

    root = Tier(3)
    runner = ControllerRunner(root)
    await runner.build()

    controller: Controller = root
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
async def test_controllers_sharing_a_connection_open_it_once():
    supervisor, handle = supervise(FakeConnection())

    class Parent(Controller):
        def __init__(self):
            super().__init__()
            self.add_sub_controller("A", LifecycleController(handle))
            self.add_sub_controller("B", LifecycleController(handle))

    runner = ControllerRunner(Parent(), [supervisor])
    await runner.build()

    assert supervisor.connection.connects == 1


@pytest.mark.asyncio
async def test_a_connection_the_runner_does_not_supervise_is_rejected():
    """It would never be opened, and so never reconnected either."""

    class LateConnector(Controller):
        async def build(self):
            self.add_sub_controller("LATE", LifecycleController(FakeConnection()))

    runner = ControllerRunner(LateConnector())

    with pytest.raises(RuntimeError, match="does not supervise"):
        await runner.build()


@pytest.mark.asyncio
async def test_a_controller_may_hold_the_connection_itself():
    """A test can hand over the raw connection, and it is still supervised."""
    supervisor, _ = supervise(FakeConnection(), reconnect_attempts=1000)
    controller = Polling(supervisor.connection)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)
        await asyncio.sleep(0.01)
        polls = controller.polls
        await asyncio.sleep(0.01)

        assert controller.polls == polls
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_connection_no_controller_holds_is_warned_about(log_records):
    supervisor, _ = supervise(FakeConnection())
    runner = ControllerRunner(LifecycleController(), [supervisor])

    await runner.build()
    try:
        assert any(
            "not held by any controller" in r["event"]
            and r["connection"] == "FakeConnection"
            for r in log_records
        )
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_connection_nothing_polls_is_warned_about(log_records):
    supervisor, handle = supervise(FakeConnection())
    runner = ControllerRunner(LifecycleController(handle), [supervisor])

    await runner.start()
    try:
        assert any("no polled attribute" in r["event"] for r in log_records)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_polled_connection_is_not_warned_about(log_records):
    supervisor, handle = supervise(FakeConnection())
    runner = ControllerRunner(Polling(handle), [supervisor])

    await runner.start()
    try:
        assert not any("no polled attribute" in r["event"] for r in log_records)
    finally:
        await runner.stop()


# The seal


@pytest.mark.asyncio
async def test_the_tree_is_sealed_after_build():
    controller = LifecycleController()
    runner = ControllerRunner(controller)
    await runner.build()

    with pytest.raises(RuntimeError, match="sealed"):
        controller.add_attribute("late", AttrR(int))


@pytest.mark.asyncio
async def test_a_sub_controller_is_sealed_too():
    child = LifecycleController()
    parent = Controller()
    parent.add_sub_controller("CHILD", child)
    runner = ControllerRunner(parent)
    await runner.build()

    with pytest.raises(RuntimeError, match="sealed"):
        child.add_sub_controller("LATE", Controller())


@pytest.mark.asyncio
async def test_adding_in_setup_raises():
    class Late(Controller):
        async def setup(self):
            self.late = AttrR(int)

    runner = ControllerRunner(Late())

    with pytest.raises(RuntimeError, match="sealed"):
        await runner.start()


@pytest.mark.asyncio
async def test_every_controller_gets_a_connected_attribute():
    supervisor, handle = supervise(FakeConnection())
    parent = Controller()
    parent.add_sub_controller("CHILD", LifecycleController(handle))
    runner = ControllerRunner(parent, [supervisor])

    apis = await runner.build()

    assert all(CONNECTED_ATTRIBUTE in api.attributes for api in apis[0].walk_api())


@pytest.mark.asyncio
async def test_a_soft_controller_is_always_connected():
    controller = LifecycleController()
    runner = ControllerRunner(controller)

    await runner.start()
    connected = controller.attributes[CONNECTED_ATTRIBUTE]
    assert isinstance(connected, AttrR)
    try:
        assert connected.readback is True
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_connected_goes_off_when_the_link_drops():
    supervisor, handle = supervise(FakeConnection(), reconnect_period=0.001)
    controller = LifecycleController(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    connected = controller.attributes[CONNECTED_ATTRIBUTE]
    assert isinstance(connected, AttrR)
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)

        await eventually(lambda: connected.readback is False)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_connected_comes_back_on_with_the_link():
    supervisor, handle = supervise(FakeConnection(), reconnect_period=0.001)
    controller = LifecycleController(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    connected = controller.attributes[CONNECTED_ATTRIBUTE]
    assert isinstance(connected, AttrR)
    try:
        link_down(supervisor)
        await asyncio.wait_for(supervisor.wait_up(), timeout=2)

        await eventually(lambda: connected.readback is True)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_controller_with_its_own_connected_member_is_rejected():
    class Clashing(Controller):
        def __init__(self):
            super().__init__()
            self.connected = AttrR(bool)

    runner = ControllerRunner(Clashing())

    with pytest.raises(RuntimeError, match="already has a member named"):
        await runner.build()


# Scans


@pytest.mark.asyncio
async def test_scans_pause_while_their_connection_is_down():
    supervisor, handle = supervise(FakeConnection(), reconnect_attempts=1000)
    controller = Polling(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)
        await asyncio.sleep(0.01)
        polls = controller.polls
        await asyncio.sleep(0.02)

        assert controller.polls == polls
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_scans_resume_after_a_reconnect():
    supervisor, handle = supervise(FakeConnection(), reconnect_period=0.001)
    controller = Polling(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        link_down(supervisor)
        await asyncio.wait_for(supervisor.wait_up(), timeout=2)
        polls = controller.polls

        await eventually(lambda: controller.polls > polls)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_child_keeps_scanning_when_its_parents_link_drops():
    """Each connection is paused on its own, not the whole tree on the root's."""
    root_supervisor, root_handle = supervise(BaseLink(), reconnect_attempts=1000)
    child_supervisor, child_handle = supervise(FakeConnection())
    root = Polling(root_handle)
    child = Polling(child_handle)
    root.add_sub_controller("CHILD", child)
    runner = ControllerRunner(root, [root_supervisor, child_supervisor])
    await runner.start()
    try:
        root_supervisor.connection.fail_connect = RuntimeError("down")
        link_down(root_supervisor)
        await asyncio.sleep(0.01)
        polls = child.polls

        await eventually(lambda: child.polls > polls)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_child_pauses_when_its_own_link_drops_under_a_healthy_parent():
    root_supervisor, root_handle = supervise(BaseLink())
    child_supervisor, child_handle = supervise(
        FakeConnection(), reconnect_attempts=1000
    )
    root = Polling(root_handle)
    child = Polling(child_handle)
    root.add_sub_controller("CHILD", child)
    runner = ControllerRunner(root, [root_supervisor, child_supervisor])
    await runner.start()
    try:
        child_supervisor.connection.fail_connect = RuntimeError("down")
        link_down(child_supervisor)
        await asyncio.sleep(0.01)
        polls = child.polls
        await asyncio.sleep(0.02)

        assert child.polls == polls
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_soft_parent_does_not_gate_its_children():
    child_supervisor, child_handle = supervise(FakeConnection())
    root = Controller()
    child = Polling(child_handle)
    root.add_sub_controller("CHILD", child)
    runner = ControllerRunner(root, [child_supervisor])
    await runner.start()
    try:
        polls = child.polls

        await eventually(lambda: child.polls > polls)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_poll_that_hits_a_dead_link_takes_the_link_down():
    supervisor, handle = supervise(FakeConnection(), reconnect_attempts=1000)
    runner = ControllerRunner(Polling(handle), [supervisor])
    await runner.start()
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        supervisor.connection.fail_io = ConnectionResetError()

        await eventually(lambda: not supervisor.up)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_polled_values_are_flagged_invalid_when_the_link_drops():
    supervisor, handle = supervise(FakeConnection(), reconnect_attempts=1000)
    controller = Polling(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)

        await eventually(lambda: controller.value.severity is Severity.INVALID)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_good_poll_after_a_reconnect_clears_the_flag():
    supervisor, handle = supervise(
        FakeConnection(), reconnect_period=0.001, reconnect_attempts=10_000
    )
    controller = Polling(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        # Hold the reconnect off until the flag has been seen, or it can be set
        # and cleared again between two checks
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)
        await eventually(lambda: controller.value.severity is Severity.INVALID)

        supervisor.connection.fail_connect = None
        await eventually(lambda: controller.value.severity is Severity.NO_ALARM)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_read_once_reads_run_again_after_a_reconnect():
    """A rebooted device can come back with different values."""
    supervisor, handle = supervise(FakeConnection(), reconnect_period=0.001)
    controller = LifecycleController(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        link_down(supervisor)

        await eventually(lambda: controller.count.readback == 2)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_setup_does_not_run_again_after_a_reconnect():
    supervisor, handle = supervise(FakeConnection(), reconnect_period=0.001)
    controller = LifecycleController(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        link_down(supervisor)
        await eventually(lambda: controller.count.readback == 2)

        assert controller.events.count("setup") == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_raising_scan_is_retried():
    calls = 0

    class Failing(Controller):
        @scan(0.001)
        async def failing(self):
            nonlocal calls
            calls += 1
            raise RuntimeError("scan error")

    runner = ControllerRunner(Failing())
    await runner.start()
    try:
        await eventually(lambda: calls > 3)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_scan_failing_the_same_way_is_logged_once(log_records):
    calls = 0

    class Failing(Controller):
        @scan(0.001)
        async def failing(self):
            nonlocal calls
            calls += 1
            raise RuntimeError("scan error")

    runner = ControllerRunner(Failing())
    await runner.start()
    try:
        await eventually(lambda: calls > 3)

        assert [r["event"] for r in log_records].count("Scan failed") == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_put_on_a_dead_link_fails_back_to_the_caller():
    supervisor, handle = supervise(FakeConnection(), reconnect_attempts=1000)
    controller = Polling(handle)
    runner = ControllerRunner(controller, [supervisor])
    await runner.start()
    try:
        supervisor.connection.fail_connect = RuntimeError("down")
        link_down(supervisor)

        with pytest.raises(DisconnectedError):
            await controller.value.set(5)
    finally:
        await runner.stop()


# Dependencies, by type


@pytest.mark.asyncio
async def test_connections_are_opened_in_dependency_order():
    """However they were declared."""
    opened: list[str] = []

    class RecordedBase(BaseLink):
        async def connect(self) -> None:
            opened.append("base")

    class RecordedLayered(LayeredLink):
        async def connect(self) -> None:
            opened.append("layered")

    layered, layered_handle = supervise(RecordedLayered())
    base, base_handle = supervise(RecordedBase())
    root = Controller()
    root.add_sub_controller("LAYERED", LifecycleController(layered_handle))
    root.add_sub_controller("BASE", LifecycleController(base_handle))
    runner = ControllerRunner(root, [layered, base])

    await runner.build()

    assert opened == ["base", "layered"]


@pytest.mark.asyncio
async def test_a_dependency_is_every_connection_of_the_type():
    first, _ = supervise(BaseLink())
    second, _ = supervise(BaseLink())
    layered, _ = supervise(LayeredLink())
    runner = ControllerRunner(LifecycleController(), [first, second, layered])

    await runner.build()

    assert layered.dependencies == [first, second]


@pytest.mark.asyncio
async def test_a_subclass_satisfies_a_dependency():
    class SpecialBase(BaseLink):
        pass

    base, _ = supervise(SpecialBase())
    layered, _ = supervise(LayeredLink())
    runner = ControllerRunner(LifecycleController(), [base, layered])

    await runner.build()

    assert layered.dependencies == [base]


@pytest.mark.asyncio
async def test_a_dependency_with_no_instance_is_not_waited_for():
    layered, _ = supervise(LayeredLink())
    runner = ControllerRunner(LifecycleController(), [layered])

    await runner.build()

    assert layered.dependencies == []


@pytest.mark.asyncio
async def test_dependencies_are_resolved_within_one_top_level_controller():
    base_a, _ = supervise(BaseLink())
    layered_a, _ = supervise(LayeredLink())
    base_b, _ = supervise(BaseLink())
    runner = ControllerRunner(
        [LifecycleController(), LifecycleController()],
        [[base_a, layered_a], [base_b]],
    )

    await runner.build()

    assert layered_a.dependencies == [base_a]


@pytest.mark.asyncio
async def test_a_dependency_cycle_is_caught_at_startup():
    class Chicken(FakeConnection):
        pass

    class Egg(FakeConnection):
        depends_on = [Chicken]

    Chicken.depends_on = [Egg]

    chicken, _ = supervise(Chicken())
    egg, _ = supervise(Egg())
    runner = ControllerRunner(LifecycleController(), [chicken, egg])

    with pytest.raises(ValueError, match="Cycle in connection dependencies"):
        await runner.build()


@pytest.mark.asyncio
async def test_a_dependent_waits_for_its_dependency_to_reconnect():
    base, base_handle = supervise(
        BaseLink(), reconnect_period=0.001, reconnect_attempts=1000
    )
    layered, layered_handle = supervise(LayeredLink(), reconnect_period=0.001)
    root = Controller()
    root.add_sub_controller("BASE", Polling(base_handle))
    root.add_sub_controller("LAYERED", Polling(layered_handle))
    runner = ControllerRunner(root, [base, layered])
    await runner.start()
    try:
        base.connection.fail_connect = RuntimeError("down")
        link_down(base)
        link_down(layered)
        await asyncio.sleep(0.02)

        assert layered.connection.connects == 1
    finally:
        await runner.stop()


# Supervisor groups


def test_one_group_per_controller_is_required_when_grouping():
    supervisor, _ = supervise(FakeConnection())

    with pytest.raises(ValueError, match="groups of supervisors"):
        ControllerRunner([LifecycleController(), LifecycleController()], [[supervisor]])


def test_a_supervisor_given_twice_is_rejected():
    supervisor, _ = supervise(FakeConnection())

    with pytest.raises(ValueError, match="twice"):
        ControllerRunner(LifecycleController(), [supervisor, supervisor])


# Stopping, and failing to start


@pytest.mark.asyncio
async def test_stop_closes_connections_in_reverse_of_opening():
    closed: list[str] = []

    class RecordedBase(BaseLink):
        async def close(self) -> None:
            closed.append("base")

    class RecordedLayered(LayeredLink):
        async def close(self) -> None:
            closed.append("layered")

    base, _ = supervise(RecordedBase())
    layered, _ = supervise(RecordedLayered())
    runner = ControllerRunner(LifecycleController(), [layered, base])
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

    supervisor, handle = supervise(UncloseableConnection())
    runner = ControllerRunner(LifecycleController(handle), [supervisor])
    await runner.start()

    await runner.stop()

    assert [(event, str(error)) for event, error in logged] == [
        ("Exception while closing connection", "no")
    ]


@pytest.mark.asyncio
async def test_stop_cancels_the_tasks():
    supervisor, handle = supervise(FakeConnection())
    runner = ControllerRunner(Polling(handle), [supervisor])
    await runner.start()
    tasks = set(supervisor._tasks)  # noqa: SLF001

    await runner.stop()
    await asyncio.sleep(0)

    assert all(task.cancelled() or task.done() for task in tasks)


@pytest.mark.asyncio
async def test_a_connection_that_fails_to_open_aborts_startup():
    supervisor, handle = supervise(FakeConnection())
    supervisor.connection.fail_connect = ConnectionRefusedError()
    runner = ControllerRunner(LifecycleController(handle), [supervisor])

    with pytest.raises(ConnectionRefusedError):
        await runner.build()


@pytest.mark.asyncio
async def test_a_connection_that_fails_to_open_closes_those_opened_before_it():
    first, _ = supervise(FakeConnection())
    second, _ = supervise(FakeConnection())
    second.connection.fail_connect = ConnectionRefusedError()
    runner = ControllerRunner(LifecycleController(), [first, second])

    with pytest.raises(ConnectionRefusedError):
        await runner.build()

    assert first.connection.closes == 1


@pytest.mark.asyncio
async def test_a_failed_build_closes_what_it_opened():
    """Startup aborts, and no task exists yet for a later `stop` to clean up."""
    supervisor, handle = supervise(FakeConnection())

    class Unbuildable(Controller):
        def __init__(self):
            self.connection = handle
            super().__init__()

        async def build(self):
            raise RuntimeError("cannot build")

    runner = ControllerRunner(Unbuildable(), [supervisor])

    with pytest.raises(RuntimeError, match="cannot build"):
        await runner.build()

    assert supervisor.connection.closes == 1


@pytest.mark.asyncio
async def test_a_failed_setup_closes_what_it_opened():
    supervisor, handle = supervise(FakeConnection())

    class Unsetuppable(Controller):
        def __init__(self):
            self.connection = handle
            super().__init__()

        async def setup(self):
            raise RuntimeError("cannot set up")

    runner = ControllerRunner(Unsetuppable(), [supervisor])

    with pytest.raises(RuntimeError, match="cannot set up"):
        await runner.start()

    assert supervisor.connection.closes == 1


@pytest.mark.asyncio
async def test_a_fatal_failure_is_reported_by_the_runner():
    supervisor, handle = supervise(
        FakeConnection(), reconnect_period=0.001, policy=DRAPolicy()
    )
    runner = ControllerRunner(LifecycleController(handle), [supervisor])
    await runner.start()
    try:
        error = FileNotFoundError("/dev/ttyACM0")
        supervisor.connection.fail_connect = error
        link_down(supervisor)

        await asyncio.wait_for(runner.fatal_error.wait(), timeout=2)

        assert runner.fatal_reason is error
    finally:
        await runner.stop()
