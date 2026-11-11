import asyncio
from dataclasses import dataclass

import pytest

from fastcs.connections import Connection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.methods import scan
from fastcs.runner import ControllerRunner
from fastcs.util import ONCE


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
    controller = ScanController()
    runner = await ControllerRunner.build([ControllerSpec(None, lambda: controller)])
    await runner.start()

    await asyncio.wait_for(controller.multiple_scans.wait(), timeout=1)
    assert controller.setup_before_read_once
    assert controller.read_once_count == 1

    await asyncio.wait_for(controller.started.wait(), timeout=1)
    await runner.stop()
    await asyncio.wait_for(controller.stopped.wait(), timeout=1)


class RecordingConnection(Connection):
    def __init__(self, events: list[str], name: str = "") -> None:
        self.events = events
        self.suffix = f" {name}" if name else ""

    async def connect(self) -> None:
        self.events.append(f"connect{self.suffix}")

    async def close(self) -> None:
        self.events.append(f"close{self.suffix}")


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


class EigerConnection(RecordingConnection):
    pass


class OdinConnection(RecordingConnection):
    depends_on = EigerConnection


class TwoConnectionController(Controller):
    def __init__(self, odin: OdinConnection, eiger: EigerConnection) -> None:
        super().__init__()


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
    controller = ScanController()
    controller.add_sub_controller("SUB", Controller())
    await ControllerRunner.build([ControllerSpec(None, lambda: controller)])

    with pytest.raises(RuntimeError, match="after it has been sealed"):
        controller.sub_controllers["SUB"].add_sub_controller("LATE", Controller())


def test_seal_twice_raises():
    controller = Controller()
    controller.seal()
    with pytest.raises(RuntimeError, match="already sealed"):
        controller.seal()
