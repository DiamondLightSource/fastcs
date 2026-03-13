import asyncio
from dataclasses import dataclass

import pytest

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.datatypes import Int
from fastcs.methods import Command, command
from fastcs.util import ONCE


@pytest.mark.asyncio
async def test_scan_tasks(controller):
    transport_options = []
    fastcs = FastCS(controller, transport_options)

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)

    for _ in range(3):
        count = controller.count
        await asyncio.sleep(0.1)
        assert controller.count > count


@pytest.mark.asyncio
async def test_controller_api_methods():
    class MyTestController(Controller):
        def __init__(self):
            super().__init__()

        async def initialise(self):
            async def do_nothing_dynamic() -> None:
                pass

            self.do_nothing_dynamic = Command(do_nothing_dynamic)

        @command()
        async def do_nothing_static(self):
            pass

    controller = MyTestController()
    transport_options = []
    fastcs = FastCS(controller, transport_options)

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)

    await controller.do_nothing_static()
    await controller.do_nothing_dynamic()

    await fastcs.controller_api.command_methods["do_nothing_static"]()
    await fastcs.controller_api.command_methods["do_nothing_dynamic"]()


@pytest.mark.asyncio
async def test_update_periods():
    @dataclass
    class AttributeIORefTimesCalled(AttributeIORef):
        update_period: float | None = None
        _times_called = 0

    class AttributeIOTimesCalled(AttributeIO[int, AttributeIORefTimesCalled]):
        async def update(self, attr: AttrR[int, AttributeIORefTimesCalled]):
            attr.io_ref._times_called += 1
            await attr.update(attr.io_ref._times_called)

    class MyController(Controller):
        update_once = AttrR(Int(), io_ref=AttributeIORefTimesCalled(update_period=ONCE))
        update_quickly = AttrR(
            Int(), io_ref=AttributeIORefTimesCalled(update_period=0.1)
        )
        update_never = AttrR(
            Int(), io_ref=AttributeIORefTimesCalled(update_period=None)
        )

    controller = MyController(ios=[AttributeIOTimesCalled()])
    fastcs = FastCS(controller, [])

    assert controller.update_quickly.get() == 0
    assert controller.update_once.get() == 0
    assert controller.update_never.get() == 0

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.5)

    assert controller.update_quickly.get() > 1
    assert controller.update_once.get() == 1
    assert controller.update_never.get() == 0

    assert len(fastcs._scan_tasks) == 1
    assert len(fastcs._initial_coros) == 1


@pytest.mark.asyncio
async def test_controller_connect_disconnect():
    class MyTestController(Controller):
        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.connected = False

    controller = MyTestController()
    fastcs = FastCS(controller, [])

    task = asyncio.create_task(fastcs.serve(interactive=False))

    # connect is called at the start of serve
    await asyncio.sleep(0.1)
    assert controller.connected

    task.cancel()

    # disconnect is called at the end of serve
    await asyncio.sleep(0.1)
    assert not controller.connected
