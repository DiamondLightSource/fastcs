import asyncio

import pytest

from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerRunner
from fastcs.controllers.runner import RECONNECT_PERIOD
from fastcs.datatypes import Int
from fastcs.methods import scan
from fastcs.util import ONCE


class LifecycleController(Controller):
    """Records every lifecycle hook the runner is supposed to call."""

    def __init__(self):
        super().__init__()
        self.events: list[str] = []
        self.count = AttrR(Int())

    async def initialise(self):
        self.events.append("initialise")

    def post_initialise(self):
        self.events.append("post_initialise")

    async def connect(self):
        self.events.append("connect")
        await super().connect()

    async def disconnect(self):
        self.events.append("disconnect")

    @scan(ONCE)
    async def read_once(self):
        self.events.append("initial")
        await self.count.update(self.count.readback + 1)


@pytest.mark.asyncio
async def test_the_runner_drives_the_whole_lifecycle():
    controller = LifecycleController()
    runner = ControllerRunner(controller)

    await runner.start()
    try:
        assert controller.events == [
            "initialise",
            "post_initialise",
            "connect",
            "initial",
        ]
        assert controller.count.readback == 1
    finally:
        await runner.stop()

    assert controller.events[-1] == "disconnect"


@pytest.mark.asyncio
async def test_setup_builds_the_apis_before_anything_connects():
    """A transport is wired to the APIs between setup and start."""
    controller = LifecycleController()
    runner = ControllerRunner(controller)

    apis = await runner.setup()

    assert [api.path for api in apis] == [[]]
    assert "count" in apis[0].attributes
    assert controller.events == ["initialise", "post_initialise"]
    assert runner.controller_apis == apis


@pytest.mark.asyncio
async def test_start_sets_up_when_setup_has_not_run():
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
        assert len(runner.controller_apis) == 2
        assert all(controller.count.readback == 1 for controller in controllers)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_stop_reports_a_failing_disconnect_without_raising():
    class UndisconnectableController(LifecycleController):
        async def disconnect(self):
            raise RuntimeError("no")

    runner = ControllerRunner(UndisconnectableController())
    await runner.start()

    await runner.stop()


@pytest.mark.asyncio
async def test_the_runner_reconnects_a_controller_that_dropped_out(monkeypatch):
    """Nothing else calls reconnect, so a paused controller would stay paused."""
    monkeypatch.setattr("fastcs.controllers.runner.RECONNECT_PERIOD", 0.01)

    class DroppingController(LifecycleController):
        reconnects = 0

        async def reconnect(self):
            type(self).reconnects += 1
            await super().reconnect()

    controller = DroppingController()
    runner = ControllerRunner(controller)
    await runner.start()
    try:
        assert controller.connected

        # What a scan task does when its callback raises
        controller._connected = False

        await asyncio.sleep(0.05)

        assert DroppingController.reconnects >= 1
        assert controller.connected
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_failing_reconnect_does_not_stop_the_runner(monkeypatch):
    monkeypatch.setattr("fastcs.controllers.runner.RECONNECT_PERIOD", 0.01)

    class UnreconnectableController(LifecycleController):
        attempts = 0

        async def reconnect(self):
            type(self).attempts += 1
            raise RuntimeError("still down")

    controller = UnreconnectableController()
    runner = ControllerRunner(controller)
    await runner.start()
    try:
        controller._connected = False
        await asyncio.sleep(0.05)

        # It keeps trying rather than dying on the first failure
        assert UnreconnectableController.attempts > 1
        assert not controller.connected
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_stop_cancels_the_tasks():
    controller = LifecycleController()
    runner = ControllerRunner(controller)
    await runner.start()
    tasks = set(runner._tasks)
    assert tasks

    await runner.stop()
    await asyncio.sleep(0)

    assert all(task.cancelled() or task.done() for task in tasks)
    assert not runner._tasks


def test_reconnect_period_is_a_second_by_default():
    assert RECONNECT_PERIOD == 1.0
