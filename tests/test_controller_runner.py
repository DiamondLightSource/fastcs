import asyncio

import pytest

from fastcs.controllers import Controller
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
    a, b = ScanController(), ScanController()
    a.set_path(["A"])
    b.set_path(["B"])
    runner = await ControllerRunner.build([a, b])

    assert [api.path for api in runner.controller_apis] == [["A"], ["B"]]


@pytest.mark.asyncio
async def test_start_runs_scans_and_stop_stops_them():
    controller = ScanController()
    runner = await ControllerRunner.build([controller])
    await runner.start()

    await asyncio.wait_for(controller.multiple_scans.wait(), timeout=1)
    assert controller.read_once_count == 1

    await asyncio.wait_for(controller.started.wait(), timeout=1)
    await runner.stop()
    await asyncio.wait_for(controller.stopped.wait(), timeout=1)
