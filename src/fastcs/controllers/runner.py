import asyncio
from collections.abc import Sequence

from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.logging import logger
from fastcs.methods import ScanCallback

RECONNECT_PERIOD = 1.0
"""Seconds between checks for a controller that has dropped its connection"""


class ControllerRunner:
    """Runs one or more `Controller` s, without serving them anywhere.

    This owns the whole controller lifecycle - initialising, connecting,
    running the initial and periodic tasks, reconnecting after a failure, and
    tidying up - and nothing about how the controllers are presented. `FastCS`
    uses it and adds transports on top; an embedded caller that only wants the
    controllers running can use it on its own::

        runner = ControllerRunner(controller)
        await runner.start()
        ...
        await runner.stop()

    Starting has two halves, because anything serving the controllers needs
    their `ControllerAPI` before the first values are read: ``setup`` initialises
    them and builds the APIs, and ``start`` connects and starts the tasks.
    Calling ``start`` on its own does both.

    **Idempotency is the caller's responsibility.** Starting a running runner,
    or stopping a stopped one, is not defined.

    Args:
        controllers: The controller(s) to run. Accepts either a single
            ``Controller`` or a sequence of them.
        loop: Optional event loop to create the tasks in

    """

    def __init__(
        self,
        controllers: Controller | Sequence[Controller],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if isinstance(controllers, Controller):
            controllers = [controllers]
        self._controllers: list[Controller] = list(controllers)
        self._loop = loop

        self._controller_apis: list[ControllerAPI] = []
        self._scan_coros: list[ScanCallback] = []
        self._initial_coros: list[ScanCallback] = []
        self._tasks: set[asyncio.Task] = set()

    @property
    def controller_apis(self) -> list[ControllerAPI]:
        """The API of each controller. Empty until ``setup`` has run."""
        return self._controller_apis

    async def setup(self) -> list[ControllerAPI]:
        """Initialise the controllers and build their APIs.

        Runs before anything connects, so that a transport can be wired to the
        APIs and catch the first readback.

        Returns:
            The API of each controller, in the order they were given

        """
        for controller in self._controllers:
            await controller.initialise()
            controller.post_initialise()

        self._controller_apis = []
        self._scan_coros = []
        self._initial_coros = []
        for controller in self._controllers:
            api, scan_coros, initial_coros = controller.create_api_and_tasks()
            self._controller_apis.append(api)
            self._scan_coros.extend(scan_coros)
            self._initial_coros.extend(initial_coros)

        return self._controller_apis

    async def start(self) -> None:
        """Connect the controllers and start their tasks.

        Runs ``setup`` first if it has not already run.
        """
        if not self._controller_apis:
            await self.setup()

        for controller in self._controllers:
            await controller.connect()

        for coro in self._initial_coros:
            await coro()

        loop = self._loop or asyncio.get_event_loop()
        self._tasks = {loop.create_task(coro()) for coro in self._scan_coros}
        self._tasks |= {
            loop.create_task(self._reconnect_loop(controller))
            for controller in self._controllers
        }

    async def stop(self) -> None:
        """Stop the tasks and disconnect the controllers."""
        self._cancel_tasks()

        for controller in self._controllers:
            try:
                await controller.disconnect()
            except Exception:
                logger.exception(
                    "Exception during disconnect", controller=controller.path
                )

    async def _reconnect_loop(self, controller: Controller) -> None:
        """Bring a controller back after its scan tasks hit an error.

        A scan task that raises marks its controller disconnected and pauses
        rather than dying, so something has to try to bring it back. That is the
        runner's job rather than the controller's, so that every controller
        reconnects the same way whether or not its author thought about it.
        """
        while True:
            await asyncio.sleep(RECONNECT_PERIOD)

            if controller.connected:
                continue

            logger.info("Attempting to reconnect", controller=controller.path)
            try:
                await controller.reconnect()
            except Exception:
                logger.exception("Reconnect failed", controller=controller.path)

    def _cancel_tasks(self) -> None:
        # ``Task.cancel`` does not raise - it returns whether the task was
        # cancellable - so the guards the old FastCS._stop_scan_tasks wrapped
        # this in never fired.
        for task in self._tasks:
            if not task.done():
                task.cancel()

        self._tasks.clear()

    def __del__(self):
        self._cancel_tasks()
