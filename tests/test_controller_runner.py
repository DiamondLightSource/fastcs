import asyncio

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
        self.multiple_scans = asyncio.Event()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

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
    assert controller.read_once_count == 1

    await asyncio.wait_for(controller.started.wait(), timeout=1)
    await runner.stop()
    await asyncio.wait_for(controller.stopped.wait(), timeout=1)


class RecordingConnection(Connection):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def connect(self) -> None:
        self.events.append("connect")

    async def close(self) -> None:
        self.events.append("close")


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
