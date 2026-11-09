import asyncio
from collections import defaultdict
from collections.abc import Iterator, Sequence
from typing import Self

from fastcs.attributes.attr_r import AttrR
from fastcs.attributes.attribute_io_ref import AttributeIORef
from fastcs.connections import Connection
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.controllers.controller_spec import ControllerSpec
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE


class ControllerRunner:
    """Runs the lifecycle of one or more top-level controllers.

    Args:
        specs: What to construct each top-level controller from

    """

    def __init__(self, specs: list[ControllerSpec]) -> None:
        self._specs = specs

        self._connections: list[Connection] = []
        self._controllers: list[Controller] = []

        self._controller_apis: list[ControllerAPI] = []
        self._initial_coros: list[ScanCallback] = []
        self._scan_coros: list[ScanCallback] = []
        self._tasks: set[asyncio.Task] = set()

    @classmethod
    async def build(cls, specs: list[ControllerSpec]) -> Self:
        """Open the connections, construct the controllers and create a runner with
        their APIs.

        Args:
            specs: What to construct each top-level controller from

        Returns:
            A runner ready to `start`, with an API per controller in
            ``controller_apis``, in the order the specs were given

        """
        runner = cls(specs)

        try:
            for spec in specs:
                for connection in _dependency_order(spec.connections):
                    introspection = await connection.connect()
                    connection._introspection = introspection  # noqa: SLF001
                    runner._connections.append(connection)

            runner._controllers = [spec.construct() for spec in specs]
            for controller in runner._controllers:
                await controller.initialise()
                controller.post_initialise()
        except BaseException:
            await runner._close_connections()
            raise

        for controller in runner._controllers:
            api = controller.build_api(controller.path)
            initial_coros, scan_coros = runner._collect_scans(controller, api)
            runner._controller_apis.append(api)
            runner._initial_coros.extend(initial_coros)
            runner._scan_coros.extend(scan_coros)

        return runner

    @property
    def controllers(self) -> list[Controller]:
        """The top-level controllers, in the order the specs were given"""
        return self._controllers

    @property
    def controller_apis(self) -> list[ControllerAPI]:
        """The API of each controller, in the order the specs were given"""
        return self._controller_apis

    async def start(self) -> None:
        """Set up the controllers, run the initial reads and start the scan tasks."""
        for controller in self._controllers:
            for sub_controller in _walk(controller):
                await sub_controller.setup()

        for coro in self._initial_coros:
            await coro()

        self._tasks = {asyncio.create_task(coro()) for coro in self._scan_coros}

    async def stop(self) -> None:
        """Stop the scan tasks and close the connections."""
        self._cancel_tasks()
        await self._close_connections()

    async def _close_connections(self) -> None:
        """Close the open connections, in reverse order."""
        while self._connections:
            connection = self._connections.pop()
            try:
                await connection.close()
            except Exception:
                logger.exception("Exception closing connection", connection=connection)

    def _collect_scans(
        self, controller: Controller, api: ControllerAPI
    ) -> tuple[list[ScanCallback], list[ScanCallback]]:
        """Find the initial reads and periodic scans in a controller's tree.

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


def _walk(controller: BaseController) -> Iterator[BaseController]:
    """A controller and every controller below it, parents first"""
    yield controller
    for sub_controller in controller.sub_controllers.values():
        yield from _walk(sub_controller)


def _dependency_types(connection: Connection) -> tuple[type[Connection], ...]:
    """A connection's ``depends_on`` as a tuple"""
    depends_on = connection.depends_on
    if isinstance(depends_on, type):
        return (depends_on,)

    return tuple(depends_on)


def _dependency_order(connections: list[Connection]) -> list[Connection]:
    """Order connections so that each comes after the connections it depends on.

    Raises:
        ValueError: If the dependencies form a cycle

    """
    unique = list({id(c): c for c in connections}.values())
    ordered: list[Connection] = []
    visiting: list[Connection] = []

    def visit(connection: Connection) -> None:
        if any(connection is c for c in ordered):
            return
        if any(connection is c for c in visiting):
            cycle = " -> ".join(type(c).__name__ for c in [*visiting, connection])
            raise ValueError(f"Cycle in connection dependencies: {cycle}")

        visiting.append(connection)
        for other in unique:
            if other is not connection and isinstance(
                other, _dependency_types(connection)
            ):
                visit(other)
        visiting.pop()
        ordered.append(connection)

    for connection in unique:
        visit(connection)

    return ordered


def _periodic_scan_coro(
    controller: Controller, period: float, scans: Sequence[ScanCallback]
) -> ScanCallback:
    """Create a coroutine to run scans at a given period

    Args:
        controller: The top-level controller whose connection gates the scans
        period: The period to run the scans at
        scans: A list of `ScanCallback` to run periodically

    Returns:
        A wrapper `ScanCallback` that runs all of the callbacks at a given period

    """

    async def scan_coro() -> None:
        while True:
            if not controller.connected:
                await asyncio.sleep(1)
                continue

            try:
                await asyncio.gather(asyncio.sleep(period), *[scan() for scan in scans])
            except Exception:
                logger.exception("Exception in scan task", period=period)
                controller.mark_disconnected()

                await asyncio.sleep(1)
                logger.error("Pausing scan tasks and waiting for reconnect")

    return scan_coro
