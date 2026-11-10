import asyncio
from collections import defaultdict
from collections.abc import Sequence

from fastcs.attributes.attr_r import AttrR
from fastcs.attributes.attribute_io_ref import AttributeIORef
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE


class ControllerRunner:
    """Runs the lifecycle of one or more `Controller` s: initialising, connecting,
    the initial reads, the scan tasks, and stopping.

    Args:
        controllers: The top-level controllers to run

    """

    def __init__(self, controllers: list[Controller]) -> None:
        self._controllers = controllers

        self._initial_coros: list[ScanCallback] = []
        self._scan_coros: list[ScanCallback] = []
        self._tasks: set[asyncio.Task] = set()

    async def build(self) -> list[ControllerAPI]:
        """Initialise the controllers and create their APIs.

        Returns:
            The API of each controller, in the order they were given

        """
        for controller in self._controllers:
            await controller.initialise()
            controller.post_initialise()

        controller_apis: list[ControllerAPI] = []
        self._initial_coros = []
        self._scan_coros = []
        for controller in self._controllers:
            api = controller.build_api(controller.path)
            initial_coros, scan_coros = self._collect_scans(controller, api)
            controller_apis.append(api)
            self._initial_coros.extend(initial_coros)
            self._scan_coros.extend(scan_coros)

        return controller_apis

    async def start(self) -> None:
        """Connect the controllers, run the initial reads and start the scan tasks."""
        for controller in self._controllers:
            await controller.connect()

        for coro in self._initial_coros:
            await coro()

        self._tasks = {asyncio.create_task(coro()) for coro in self._scan_coros}

    async def stop(self) -> None:
        """Stop the scan tasks and disconnect the controllers."""
        self._cancel_tasks()

        for controller in self._controllers:
            try:
                await controller.disconnect()
            except Exception:
                logger.exception(
                    "Exception during disconnect", controller=controller.path
                )

    def _collect_scans(
        self, controller: Controller, api: ControllerAPI
    ) -> tuple[list[ScanCallback], list[ScanCallback]]:
        """Find the initial reads and periodic scans in a controller's tree.

        Periodic scans are grouped into one task per period for each top-level
        controller.

        Returns:
            The initial coroutines, and one periodic coroutine per period

        """
        initial_coros: list[ScanCallback] = []
        scan_dict: dict[float, list[ScanCallback]] = defaultdict(list)

        for sub_api in api.walk_api():
            for method in sub_api.scan_methods.values():
                if method.period is ONCE:
                    initial_coros.append(method.fn)
                else:
                    scan_dict[method.period].append(method.fn)

            for attribute in sub_api.attributes.values():
                match attribute:
                    case AttrR(_io_ref=AttributeIORef(update_period=update_period)):
                        if update_period is ONCE:
                            initial_coros.append(attribute.bind_update_callback())
                        elif update_period is not None:
                            scan_dict[update_period].append(
                                attribute.bind_update_callback()
                            )

        scan_coros = [
            _periodic_scan_coro(controller, period, scans)
            for period, scans in scan_dict.items()
        ]
        return initial_coros, scan_coros

    def _cancel_tasks(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()

        self._tasks.clear()

    def __del__(self):
        self._cancel_tasks()


def _periodic_scan_coro(
    controller: Controller, period: float, scans: Sequence[ScanCallback]
) -> ScanCallback:
    """Create a coroutine to run scans at a given period

    If a scan raises, the exception is logged and the controller is marked
    disconnected, which pauses every scan task of that controller until its
    ``reconnect`` hook marks it connected again.

    Args:
        controller: The top-level controller whose connection gates the scans
        period: The period to run the scans at
        scans: A list of `ScanCallback` to run periodically

    Returns:
        A wrapper `ScanCallback` that runs all of the callbacks at a given period

    """

    async def scan_coro() -> None:
        while True:
            if not controller._connected:  # noqa: SLF001
                await asyncio.sleep(1)
                continue

            try:
                await asyncio.gather(asyncio.sleep(period), *[scan() for scan in scans])
            except Exception:
                logger.exception("Exception in scan task", period=period)
                controller._connected = False  # noqa: SLF001

                await asyncio.sleep(1)
                logger.error("Pausing scan tasks and waiting for reconnect")

    return scan_coro
