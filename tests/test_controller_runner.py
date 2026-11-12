import asyncio
from dataclasses import dataclass

import pytest

from fastcs.connections import Connection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.exceptions import FatalError
from fastcs.methods import scan
from fastcs.runner import ControllerRunner, _structure
from fastcs.util import ONCE


class ScanController(Controller):
    def __init__(self) -> None:
        super().__init__()
        self.read_once_count = 0
        self.scan_count = 0
        self.setup_before_read_once = False
        self.multiple_scans = asyncio.Event()
        self.scanned = asyncio.Event()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def setup(self) -> None:
        self.setup_before_read_once = self.read_once_count == 0

    @scan(ONCE)
    async def read_once(self) -> None:
        self.read_once_count += 1

    @scan(0.01)
    async def counter(self) -> None:
        self.scan_count += 1
        self.scanned.set()
        if self.scan_count > 1:
            self.multiple_scans.set()

    # Different period to counter, so it runs in its own task and doesn't block it
    @scan(0.02)
    async def block(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped.set()
            raise


@pytest.mark.asyncio
async def test_build_creates_an_api_per_controller():
    runner = await ControllerRunner.build(
        [ControllerSpec("A", ScanController), ControllerSpec("B", ScanController)]
    )

    assert [api.path for api in runner.controller_apis] == [["A"], ["B"]]


@pytest.mark.asyncio
async def test_start_runs_scans_and_stop_stops_them():
    runner = await ControllerRunner.build([ControllerSpec("A", ScanController)])
    controller = runner.controllers[0]
    assert isinstance(controller, ScanController)
    await runner.start()

    await asyncio.wait_for(controller.multiple_scans.wait(), timeout=1)
    assert controller.setup_before_read_once
    assert controller.read_once_count == 1

    await asyncio.wait_for(controller.started.wait(), timeout=1)
    await runner.stop()
    assert controller.stopped.is_set()


async def _wait_for_scans(controller: ScanController, count: int) -> None:
    """Wait until the controller's counter scan has run at least ``count`` times"""
    while controller.scan_count < count:
        controller.scanned.clear()
        await controller.scanned.wait()


@pytest.mark.asyncio
async def test_clearing_a_scan_gate_pauses_only_that_controllers_scans():
    paused, running = ScanController(), ScanController()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", lambda: paused), ControllerSpec("B", lambda: running)]
    )
    await runner.start()
    await asyncio.wait_for(_wait_for_scans(paused, 2), timeout=1)

    runner._scan_gates[id(paused)].clear()
    # Same period, so two cycles of the other let a paused cycle under way finish
    await asyncio.wait_for(_wait_for_scans(running, running.scan_count + 2), timeout=1)
    paused_count = paused.scan_count
    await asyncio.wait_for(_wait_for_scans(running, running.scan_count + 3), timeout=1)
    assert paused.scan_count == paused_count

    runner._scan_gates[id(paused)].set()
    await asyncio.wait_for(_wait_for_scans(paused, paused_count + 1), timeout=1)
    await runner.stop()


class RecordingConnection(Connection):
    def __init__(self, events: list[str], name: str = "") -> None:
        self.events = events
        self.suffix = f" {name}" if name else ""

    async def connect(self) -> None:
        self.events.append(f"connect{self.suffix}")

    async def close(self) -> None:
        self.events.append(f"close{self.suffix}")


class HoldingController(Controller):
    def __init__(self, connection: Connection) -> None:
        super().__init__()
        self.connection = connection
        self.reads = 0

    @scan(ONCE)
    async def read_once(self) -> None:
        self.reads += 1


class ConnectedController(Controller):
    def __init__(self, connection: RecordingConnection) -> None:
        super().__init__()
        self.connection = connection
        connection.events.append("construct")


@pytest.mark.asyncio
async def test_connection_opened_before_construction_and_closed_on_stop():
    events: list[str] = []
    connection = RecordingConnection(events)
    runner = await ControllerRunner.build(
        [ControllerSpec("A", ConnectedController, connection)]
    )

    controller = runner.controllers[0]
    assert isinstance(controller, ConnectedController)
    assert controller.connection is connection
    assert controller.path == ["A"]
    assert events == ["connect", "construct"]

    await runner.start()
    await runner.stop()

    assert events == ["connect", "construct", "close"]


class UnheldController(Controller):
    def __init__(self, connection: Connection) -> None:
        super().__init__()


class TwoHeldController(Controller):
    def __init__(self, a: RecordingConnection, b: RecordingConnection) -> None:
        super().__init__()
        self.a = a
        self.b = b


class UnopenedController(Controller):
    def __init__(self) -> None:
        super().__init__()
        self.connection = RecordingConnection([])


@pytest.mark.parametrize(
    "spec, message",
    [
        (
            ControllerSpec("A", UnheldController, RecordingConnection([])),
            "No controller holds",
        ),
        (
            ControllerSpec(
                "A", TwoHeldController, RecordingConnection([]), RecordingConnection([])
            ),
            "more than one connection",
        ),
        (ControllerSpec("A", UnopenedController), "wasn't opened by the framework"),
    ],
)
@pytest.mark.asyncio
async def test_build_raises_if_connections_break_the_holding_rules(
    spec: ControllerSpec, message: str
):
    with pytest.raises(ValueError, match=message):
        await ControllerRunner.build([spec])


class FailingController(Controller):
    def __init__(self, connection: RecordingConnection) -> None:
        raise RuntimeError("construction failed")


@pytest.mark.asyncio
async def test_connections_closed_if_build_fails():
    events: list[str] = []
    with pytest.raises(RuntimeError, match="construction failed"):
        await ControllerRunner.build(
            [ControllerSpec("A", FailingController, RecordingConnection(events))]
        )

    assert events == ["connect", "close"]


class FailingIntrospectConnection(RecordingConnection):
    async def introspect(self) -> None:
        raise RuntimeError("introspect failed")


@pytest.mark.asyncio
async def test_connection_closed_if_introspect_fails():
    events: list[str] = []
    with pytest.raises(RuntimeError, match="introspect failed"):
        await ControllerRunner.build(
            [
                ControllerSpec(
                    "A", ConnectedController, FailingIntrospectConnection(events)
                )
            ]
        )

    assert events == ["connect", "close"]


@pytest.mark.asyncio
async def test_connection_shared_between_specs_raises_before_anything_opens():
    events: list[str] = []
    connection = RecordingConnection(events)
    with pytest.raises(ValueError, match="used by controllers 'A' and 'B'"):
        await ControllerRunner.build(
            [
                ControllerSpec("A", ConnectedController, connection),
                ControllerSpec("B", ConnectedController, connection),
            ]
        )

    assert events == []


class EigerConnection(RecordingConnection):
    pass


class OdinConnection(RecordingConnection):
    depends_on = EigerConnection


class TwoConnectionController(Controller):
    def __init__(self, odin: OdinConnection, eiger: EigerConnection) -> None:
        super().__init__()
        self.ODIN = HoldingController(odin)
        self.EIGER = HoldingController(eiger)


@pytest.mark.asyncio
async def test_connections_opened_in_dependency_order_and_closed_in_reverse():
    events: list[str] = []
    runner = await ControllerRunner.build(
        [
            ControllerSpec(
                "A",
                TwoConnectionController,
                odin=OdinConnection(events, "odin"),
                eiger=EigerConnection(events, "eiger"),
            )
        ]
    )
    await runner.start()
    await runner.stop()

    assert events == ["connect eiger", "connect odin", "close odin", "close eiger"]


class ConnectionA(RecordingConnection):
    pass


class ConnectionB(RecordingConnection):
    depends_on = [ConnectionA]


ConnectionA.depends_on = [ConnectionB]


class CycleController(Controller):
    def __init__(self, a: ConnectionA, b: ConnectionB) -> None:
        super().__init__()


@pytest.mark.asyncio
async def test_dependency_cycle_raises_before_anything_opens():
    events: list[str] = []
    with pytest.raises(ValueError, match="Cycle in connection dependencies"):
        await ControllerRunner.build(
            [
                ControllerSpec(
                    "A",
                    CycleController,
                    ConnectionA(events, "a"),
                    ConnectionB(events, "b"),
                )
            ]
        )

    assert events == []


@dataclass
class DeviceIntrospection:
    parameters: dict[str, int]


class IntrospectingConnection(Connection[DeviceIntrospection]):
    async def connect(self) -> None:
        pass

    async def introspect(self) -> DeviceIntrospection:
        return DeviceIntrospection({"gain": 2})

    async def close(self) -> None:
        pass


class IntrospectedController(Controller):
    def __init__(self, connection: IntrospectingConnection) -> None:
        super().__init__()
        self.connection = connection
        self.parameters = connection.introspection.parameters


@pytest.mark.asyncio
async def test_introspection_available_when_controller_constructed():
    connection = IntrospectingConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", IntrospectedController, connection)]
    )

    controller = runner.controllers[0]
    assert isinstance(controller, IntrospectedController)
    assert controller.parameters == {"gain": 2}
    assert connection.introspection == DeviceIntrospection({"gain": 2})


def test_structure_counts_fields_and_keys_but_not_values():
    assert _structure(DeviceIntrospection({"gain": 2})) == _structure(
        DeviceIntrospection({"gain": 3})
    )
    assert _structure(DeviceIntrospection({"gain": 2})) != _structure(
        DeviceIntrospection({"offset": 2})
    )


class ChangingConnection(IntrospectingConnection):
    def __init__(self) -> None:
        self.introspections = 0

    async def introspect(self) -> DeviceIntrospection:
        self.introspections += 1
        if self.introspections == 1:
            return DeviceIntrospection({"gain": 2})
        return DeviceIntrospection({"offset": 2})


@pytest.mark.asyncio
async def test_introspection_changing_structure_on_reconnect_is_fatal():
    connection = ChangingConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", IntrospectedController, connection)]
    )
    await runner.start()

    connection.state.mark_down()
    with pytest.raises(FatalError, match="changed structure"):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)
    await runner.stop()


def test_introspection_before_connecting_raises():
    with pytest.raises(RuntimeError, match="no introspection until the framework"):
        _ = IntrospectingConnection().introspection


def test_introspection_set_twice_raises():
    connection = IntrospectingConnection()
    connection.introspection = DeviceIntrospection({"gain": 2})
    with pytest.raises(RuntimeError, match="already set"):
        connection.introspection = DeviceIntrospection({"gain": 3})


@pytest.mark.asyncio
async def test_build_maps_each_connection_to_the_controllers_holding_it():
    class Holder(Controller):
        def __init__(self, connection: Connection) -> None:
            super().__init__()
            self.connection = connection

    class Parent(Controller):
        def __init__(self, connection: Connection) -> None:
            super().__init__()
            self.A = Holder(connection)
            self.B = Holder(connection)

    connection = RecordingConnection([])
    runner = await ControllerRunner.build([ControllerSpec("P", Parent, connection)])

    holders = list(runner.controllers[0].sub_controllers.values())
    assert runner._controllers_by_connection[id(connection)] == holders


@pytest.mark.asyncio
async def test_controller_tree_sealed_after_build():
    class ParentController(Controller):
        def __init__(self) -> None:
            super().__init__()
            self.add_sub_controller("SUB", Controller())

    runner = await ControllerRunner.build([ControllerSpec("A", ParentController)])
    controller = runner.controllers[0]

    with pytest.raises(RuntimeError, match="after it has been sealed"):
        controller.sub_controllers["SUB"].add_sub_controller("LATE", Controller())


def test_seal_twice_raises():
    controller = Controller()
    controller.seal()
    with pytest.raises(RuntimeError, match="already sealed"):
        controller.seal()


class FlakyConnection(Connection):
    reconnect_period = 0

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.connects = 0
        self.reconnected = asyncio.Event()

    async def connect(self) -> None:
        self.connects += 1
        if self.connects == 1:
            return
        if self.failures:
            self.failures -= 1
            raise ConnectionError("Device is off")
        self.reconnected.set()

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_watcher_reconnects_then_marks_controllers_up():
    connection = FlakyConnection(failures=1)
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]
    assert isinstance(controller, HoldingController)

    connection.state.mark_down()
    await asyncio.wait_for(connection.reconnected.wait(), timeout=1)
    await asyncio.wait_for(runner._scan_gates[id(controller)].wait(), timeout=1)

    assert connection.connects == 3
    assert controller.connected.get() is True
    assert controller.reads == 2
    await runner.stop()


@pytest.mark.asyncio
async def test_watcher_gives_up_and_leaves_controllers_down():
    connection = FlakyConnection(failures=0)
    connection.reconnect_attempts = 0
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]

    connection.state.mark_down()
    await asyncio.wait_for(runner._watchers[id(connection)], timeout=1)

    assert connection.connects == 1
    assert controller.connected.get() is False
    assert not runner._scan_gates[id(controller)].is_set()
    await runner.stop()


class HeldEigerConnection(EigerConnection):
    def __init__(self, events: list[str], name: str = "") -> None:
        super().__init__(events, name)
        self.connecting = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def connect(self) -> None:
        self.connecting.set()
        await self.release.wait()
        await super().connect()


@pytest.mark.asyncio
async def test_reconnect_waits_for_dependencies_to_be_up():
    events: list[str] = []
    eiger = HeldEigerConnection(events, "eiger")
    odin = OdinConnection(events, "odin")
    runner = await ControllerRunner.build(
        [ControllerSpec("A", TwoConnectionController, odin=odin, eiger=eiger)]
    )
    await runner.start()
    events.clear()
    eiger.connecting.clear()
    eiger.release.clear()

    eiger.state.mark_down()
    odin.state.mark_down()
    await asyncio.wait_for(eiger.connecting.wait(), timeout=1)
    assert events == ["close eiger"]

    eiger.release.set()
    await asyncio.wait_for(odin.state.wait_up(), timeout=1)
    assert events == ["close eiger", "connect eiger", "close odin", "connect odin"]
    await runner.stop()
