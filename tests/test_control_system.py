import asyncio
from dataclasses import dataclass

import pytest

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR
from fastcs.connections import Connection
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import Int
from fastcs.methods import Command, command
from fastcs.util import ONCE
from tests.assertable_controller import MyTestController


@pytest.mark.asyncio
async def test_scan_tasks(controller_spec):
    loop = asyncio.get_event_loop()
    fastcs = FastCS([controller_spec], [], loop)

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)
    controller = fastcs._runner.controllers[0]
    assert isinstance(controller, MyTestController)

    for _ in range(3):
        count = controller.count
        await asyncio.sleep(0.1)
        assert controller.count > count


@pytest.mark.asyncio
async def test_controller_api_methods():
    class MyTestController(Controller):
        def __init__(self):
            super().__init__()

            async def do_nothing_dynamic() -> None:
                pass

            self.do_nothing_dynamic = Command(do_nothing_dynamic)

        @command()
        async def do_nothing_static(self):
            pass

    loop = asyncio.get_event_loop()
    fastcs = FastCS([ControllerSpec("A", MyTestController)], [], loop)

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)
    controller = fastcs._runner.controllers[0]
    assert isinstance(controller, MyTestController)

    await controller.do_nothing_static()
    await controller.do_nothing_dynamic()

    await fastcs._runner.controller_apis[0].command_methods["do_nothing_static"]()
    await fastcs._runner.controller_apis[0].command_methods["do_nothing_dynamic"]()


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
    loop = asyncio.get_event_loop()

    fastcs = FastCS([ControllerSpec("A", lambda: controller)], [], loop)

    assert controller.update_quickly.get() == 0
    assert controller.update_once.get() == 0
    assert controller.update_never.get() == 0

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.5)

    assert controller.update_quickly.get() > 1
    assert controller.update_once.get() == 1
    assert controller.update_never.get() == 0

    assert len(fastcs._runner._tasks) == 1
    assert sum(map(len, fastcs._runner._initial_coros.values())) == 1


class ClosingConnection(Connection):
    closed = False

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FailingSetupController(Controller):
    def __init__(self, connection: ClosingConnection) -> None:
        super().__init__()
        self.connection = connection

    async def setup(self) -> None:
        raise RuntimeError("setup failed")


@pytest.mark.asyncio
async def test_connections_closed_if_startup_fails():
    connection = ClosingConnection()
    fastcs = FastCS([ControllerSpec("A", FailingSetupController, connection)], [])

    with pytest.raises(RuntimeError, match="setup failed"):
        await fastcs.serve(interactive=False)

    assert connection.closed
