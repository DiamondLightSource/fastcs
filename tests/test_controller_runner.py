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
async def test_build_returns_an_api_per_controller():
    a, b = ScanController(), ScanController()
    a.set_path(["A"])
    b.set_path(["B"])
    runner = ControllerRunner([a, b])

    apis = await runner.build()

    assert runner.controller_apis == apis
    assert [api.path for api in apis] == [["A"], ["B"]]


@pytest.mark.asyncio
async def test_start_runs_scans_and_stop_stops_them():
    controller = ScanController()
    runner = ControllerRunner(controller)

    await runner.build()
    await runner.start()
    await asyncio.sleep(0.1)

    assert controller.read_once_count == 1
    assert controller.scan_count > 1

    await runner.stop()
    count = controller.scan_count
    await asyncio.sleep(0.05)

    assert controller.scan_count == count
