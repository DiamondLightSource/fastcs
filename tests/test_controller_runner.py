import asyncio

import pytest

from fastcs.controllers import Controller
from fastcs.methods import scan
from fastcs.runner import ControllerRunner
from fastcs.util import ONCE


class ScanController(Controller):
    read_once_count = 0
    scan_count = 0

    @scan(ONCE)
    async def read_once(self) -> None:
        self.read_once_count += 1

    @scan(0.01)
    async def counter(self) -> None:
        self.scan_count += 1


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
    await asyncio.sleep(0.1)

    assert controller.read_once_count == 1
    assert controller.scan_count > 1

    await runner.stop()
    count = controller.scan_count
    await asyncio.sleep(0.05)

    assert controller.scan_count == count
