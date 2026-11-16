import asyncio

import pytest

from fastcs.connections import Connection, ConnectionSpec, Introspection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.exceptions import IntrospectionMismatchError
from fastcs.methods import scan
from fastcs.runner import ControllerRunner
from fastcs.util import ONCE
from tests.example_connections import (
    DeviceIntrospection,
    EigerConnection,
    HoldingController,
    IntrospectedController,
    IntrospectingConnection,
    OdinConnection,
    RecordingConnection,
    TwoConnectionController,
)


class ScanController(Controller):
    def __init__(self) -> None:
        super().__init__()
        self.read_once_count = 0
        self.scan_count = 0
        self.setup_before_read_once = False
        self.multiple_scans = asyncio.Event()
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
    async def introspect(self) -> Introspection:
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


class BareIntrospectConnection(RecordingConnection):
    async def introspect(self) -> Introspection:
        return {"gain": 2}  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_introspect_not_returning_introspection_raises():
    with pytest.raises(TypeError, match="returned dict, not an Introspection"):
        await ControllerRunner.build(
            [ControllerSpec("A", ConnectedController, BareIntrospectConnection([]))]
        )


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


def test_introspection_before_connecting_raises():
    with pytest.raises(RuntimeError, match="no introspection until the framework"):
        _ = IntrospectingConnection().introspection


def test_introspection_set_twice_raises():
    connection = IntrospectingConnection()
    connection.introspection = DeviceIntrospection({"gain": 2})
    with pytest.raises(RuntimeError, match="already set"):
        connection.introspection = DeviceIntrospection({"gain": 3})


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


class MetaConnection(Connection):
    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass


class DetectorConnection(MetaConnection):
    async def introspect(self) -> Introspection:
        subconnections = [
            ConnectionSpec("meta", MetaConnection),
            ConnectionSpec("unused", MetaConnection),
        ]
        return Introspection(None, subconnections=subconnections)


class DetectorController(Controller):
    def __init__(
        self, detector: DetectorConnection, detector_meta: MetaConnection | None = None
    ) -> None:
        super().__init__()
        self.detector = detector
        self.meta = HoldingController(detector_meta) if detector_meta else None


@pytest.mark.asyncio
async def test_discovered_connection_is_opened_and_passed_by_name():
    runner = await ControllerRunner.build(
        [ControllerSpec("A", DetectorController, DetectorConnection())]
    )
    controller = runner.controllers[0]
    assert isinstance(controller, DetectorController) and controller.meta

    meta = controller.meta.connection
    assert type(meta) is MetaConnection
    assert runner._names[id(meta)] == "detector_meta"
    assert len(runner._connections) == 2


@pytest.mark.asyncio
async def test_connection_given_as_none_is_not_discovered():
    runner = await ControllerRunner.build(
        [
            ControllerSpec(
                "A", DetectorController, DetectorConnection(), detector_meta=None
            )
        ]
    )
    controller = runner.controllers[0]
    assert isinstance(controller, DetectorController)

    assert controller.meta is None
    assert len(runner._connections) == 1


@pytest.mark.asyncio
async def test_given_connection_is_used_instead_of_discovered():
    given = MetaConnection()
    runner = await ControllerRunner.build(
        [
            ControllerSpec(
                "A", DetectorController, DetectorConnection(), detector_meta=given
            )
        ]
    )
    controller = runner.controllers[0]
    assert isinstance(controller, DetectorController) and controller.meta

    assert controller.meta.connection is given
    assert len(runner._connections) == 2


class PortConnection(MetaConnection):
    def __init__(self, port: int) -> None:
        self.port = port


class MovingDetectorConnection(DetectorConnection):
    reconnect_attempts = 10
    reconnect_period = 0

    def __init__(self) -> None:
        self.introspections = 0

    async def introspect(self) -> Introspection:
        self.introspections += 1
        meta = ConnectionSpec("meta", PortConnection, port=5000 + self.introspections)
        return Introspection(None, subconnections=[meta])


@pytest.mark.asyncio
async def test_discovered_connection_changing_on_reconnect_is_fatal():
    connection = MovingDetectorConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", DetectorController, connection)]
    )
    await runner.start()

    connection.state.mark_down()
    with pytest.raises(IntrospectionMismatchError, match="port=5002.*port=5001"):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)
    await runner.stop()


class CachedDetectorConnection(DetectorConnection):
    reconnect_attempts = 10
    reconnect_period = 0

    def __init__(self) -> None:
        meta = ConnectionSpec("meta", MetaConnection)
        self.cached = Introspection(None, subconnections=[meta])

    async def introspect(self) -> Introspection:
        return self.cached


@pytest.mark.asyncio
async def test_introspection_returning_the_same_specs_still_reconnects():
    connection = CachedDetectorConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", DetectorController, connection)]
    )
    await runner.start()

    connection.state.mark_down()
    await asyncio.wait_for(connection.state.wait_up(), timeout=1)
    assert connection.cached.subconnections[0].name == "meta"
    await runner.stop()


class LateConnection(MetaConnection):
    pass


class LateDiscoveringConnection(MetaConnection):
    async def introspect(self) -> Introspection:
        late = ConnectionSpec("late", LateConnection)
        return Introspection(None, subconnections=[late])


class NeedsLateConnection(MetaConnection):
    depends_on = LateConnection

    def __init__(self) -> None:
        self.opened = False

    async def connect(self) -> None:
        self.opened = True


class LateController(Controller):
    def __init__(
        self,
        parent: LateDiscoveringConnection,
        needs: NeedsLateConnection,
        parent_late: LateConnection | None = None,
    ) -> None:
        super().__init__()


@pytest.mark.asyncio
async def test_depending_on_a_connection_only_discovered_raises_before_opening():
    needs = NeedsLateConnection()
    message = "needs depends on LateConnection, but parent_late can only be discovered"
    with pytest.raises(ValueError, match=message):
        await ControllerRunner.build(
            [ControllerSpec("A", LateController, LateDiscoveringConnection(), needs)]
        )

    assert not needs.opened


class NestedLateConnection(LateDiscoveringConnection):
    depends_on = LateConnection


class NestingConnection(MetaConnection):
    async def introspect(self) -> Introspection:
        nested = ConnectionSpec("nested", NestedLateConnection)
        return Introspection(None, subconnections=[nested])


class NestingController(Controller):
    def __init__(
        self,
        parent: NestingConnection,
        parent_nested: NestedLateConnection | None = None,
        parent_nested_late: LateConnection | None = None,
    ) -> None:
        super().__init__()


@pytest.mark.asyncio
async def test_discovered_connection_depending_on_one_discovered_later_raises():
    message = "parent_nested depends on parent_nested_late, but"
    with pytest.raises(ValueError, match=message):
        await ControllerRunner.build(
            [ControllerSpec("A", NestingController, NestingConnection())]
        )
