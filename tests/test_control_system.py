import asyncio

import pytest

from fastcs.attributes import AttrR, NotPolled, Polled
from fastcs.connections import Connection
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.controllers.runner import IntrospectionMismatchError
from fastcs.methods import Command, command
from fastcs.util import ONCE


@pytest.mark.asyncio
async def test_scan_tasks(controller):
    loop = asyncio.get_event_loop()
    transport_options = []
    fastcs = FastCS(controller, transport_options, loop)

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

        async def build(self):
            async def do_nothing_dynamic() -> None:
                pass

            self.do_nothing_dynamic = Command(do_nothing_dynamic)

        @command()
        async def do_nothing_static(self):
            pass

    controller = MyTestController()
    loop = asyncio.get_event_loop()
    transport_options = []
    fastcs = FastCS(controller, transport_options, loop)

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)

    await controller.do_nothing_static()
    await controller.do_nothing_dynamic()

    await fastcs.controller_apis[0].command_methods["do_nothing_static"]()
    await fastcs.controller_apis[0].command_methods["do_nothing_dynamic"]()


@pytest.mark.asyncio
async def test_update_periods():
    times_called = {"once": 0, "quickly": 0, "never": 0}

    async def get_once():
        times_called["once"] += 1
        return times_called["once"]

    async def get_quickly():
        times_called["quickly"] += 1
        return times_called["quickly"]

    async def get_never():
        times_called["never"] += 1
        return times_called["never"]

    class MyController(Controller):
        def __init__(self):
            super().__init__()
            self.update_once = AttrR(int, getter=Polled(get_once, period=ONCE))
            self.update_quickly = AttrR(int, getter=Polled(get_quickly, period=0.1))
            self.update_never = AttrR(int, getter=NotPolled(get_never))

    controller = MyController()
    loop = asyncio.get_event_loop()

    fastcs = FastCS(controller, [], loop)

    assert controller.update_quickly.readback == 0
    assert controller.update_once.readback == 0
    assert controller.update_never.readback == 0

    asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.5)

    assert controller.update_quickly.readback > 1
    assert controller.update_once.readback == 1
    assert controller.update_never.readback == 0

    # One periodic scan task per distinct period
    assert len(fastcs._runner._scan_coros) == 1
    assert len(fastcs._runner._initial_coros) == 1


@pytest.mark.asyncio
async def test_serve_opens_and_closes_the_connection():
    """Opening and closing the link is the runner's job, not the controller's."""

    class MyTestConnection(Connection[None]):
        def __init__(self):
            super().__init__()
            self.open = False

        async def connect(self) -> None:
            self.open = True

        async def close(self) -> None:
            self.open = False

    class MyTestController(Controller):
        def __init__(self, connection):
            self.connection = connection
            super().__init__()

    connection = MyTestConnection()
    controller = MyTestController(connection)

    loop = asyncio.get_event_loop()
    fastcs = FastCS(controller, [], loop)

    task = asyncio.create_task(fastcs.serve(interactive=False))

    # The runner opens every connection at the start of serve
    await asyncio.sleep(0.1)
    assert connection.open
    assert controller.connected

    task.cancel()

    # ...and closes them at the end of it
    await asyncio.sleep(0.1)
    assert not connection.open


@pytest.mark.asyncio
async def test_a_fatal_runner_condition_comes_out_of_serve():
    """Not `sys.exit`: an embedded FastCS must be able to see this and decide."""

    class MyTestConnection(Connection[str]):
        def __init__(self):
            super().__init__()
            self.introspection = "v1"

        async def connect(self) -> str:
            return self.introspection

        async def close(self) -> None: ...

    class MyTestController(Controller):
        def __init__(self, connection):
            self.connection = connection
            super().__init__()

    connection = MyTestConnection()
    connection.reconnect_period = 0.001
    fastcs = FastCS(MyTestController(connection), [], asyncio.get_event_loop())

    task = asyncio.create_task(fastcs.serve(interactive=False))
    await asyncio.sleep(0.1)

    # The device comes back describing itself differently, which `build` cannot
    # be re-run to accommodate.
    connection.introspection = "v2"
    connection.set_disconnected()

    with pytest.raises(IntrospectionMismatchError, match="describing itself"):
        await asyncio.wait_for(task, timeout=5)
