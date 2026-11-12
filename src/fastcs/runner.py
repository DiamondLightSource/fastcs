import asyncio
from collections import defaultdict
from collections.abc import Iterator, Sequence
from typing import Self

from fastcs.attributes.attr_r import AttrR
from fastcs.connections import Connection, ConnectionState
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.controllers.controller_spec import ControllerSpec
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE


class ControllerRunner:
    """Runs the lifecycle of one or more top-level controllers."""

    def __init__(self) -> None:
        self._connections: list[Connection] = []
        self._controllers: list[Controller] = []
        # id of each connection -> the controllers that hold it, in tree order
        self._controllers_by_connection: dict[int, list[BaseController]] = defaultdict(
            list
        )

        # id of each connection -> the connections it waits for when reconnecting
        self._dependencies: dict[int, list[Connection]] = {}

        self._controller_apis: list[ControllerAPI] = []
        # id of each controller -> its read-once reads
        self._initial_coros: dict[int, list[ScanCallback]] = defaultdict(list)
        self._scan_coros: list[ScanCallback] = []
        # id of each controller -> its scan gate, set while its scans may run
        self._scan_gates: dict[int, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()
        # id of each connection -> its watcher
        self._watchers: dict[int, asyncio.Task] = {}

    @classmethod
    async def build(cls, specs: list[ControllerSpec]) -> Self:
        """Open the connections, construct the controllers and create a runner with
        their APIs.

        Args:
            specs: What to construct each top-level controller from

        Returns:
            A runner ready to `start`, with an API per controller in
            ``controller_apis``, in the order the specs were given

        Raises:
            ValueError: If a connection is shared between specs

        """
        _check_connections_not_shared(specs)
        runner = cls()

        try:
            for spec in specs:
                for connection in _dependency_order(spec.connections):
                    connection.state = ConnectionState()
                    await connection.connect()
                    runner._connections.append(connection)
                    connection.introspection = await connection.introspect()
                    runner._dependencies[id(connection)] = _dependencies(
                        connection, spec.connections
                    )

            runner._controllers = [spec.construct() for spec in specs]
            runner._seal()
            runner._map_connection_holders()

            for controller in runner._controllers:
                runner._controller_apis.append(controller.build_api(controller.path))
                runner._collect_scans(controller)
        except BaseException:
            await runner._close_connections()
            raise

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

        for reads in self._initial_coros.values():
            for read in reads:
                await read()

        self._tasks = {asyncio.create_task(coro()) for coro in self._scan_coros}
        self._watchers = {
            id(connection): asyncio.create_task(self._watch(connection))
            for connection in self._connections
        }

    async def stop(self) -> None:
        """Stop the scan tasks and watchers, and close the connections."""
        tasks = [*self._tasks, *self._watchers.values()]
        self._cancel_tasks()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_connections()

    def _seal(self) -> None:
        """Seal every controller in every tree."""
        for controller in self._controllers:
            for sub_controller in _walk(controller):
                sub_controller.seal()

    def _map_connection_holders(self) -> None:
        """Record which controllers hold each connection.

        Raises:
            ValueError: If a controller holds a connection the runner didn't open, or
                more than one connection, or if no controller holds a connection

        """
        opened = {id(connection) for connection in self._connections}
        for controller in self._controllers:
            for sub_controller in _walk(controller):
                held = _held_connections(sub_controller)
                for connection in held:
                    if id(connection) not in opened:
                        raise ValueError(
                            f"{sub_controller} holds a {type(connection).__name__} "
                            "that wasn't opened by the framework. Declare it as an "
                            "argument of the top-level controller instead."
                        )
                if len(held) > 1:
                    names = ", ".join(type(c).__name__ for c in held)
                    raise ValueError(
                        f"{sub_controller} holds more than one connection ({names}). "
                        "Give each connection its own sub controller instead."
                    )
                if held:
                    self._controllers_by_connection[id(held[0])].append(sub_controller)

        for connection in self._connections:
            if id(connection) not in self._controllers_by_connection:
                raise ValueError(
                    f"No controller holds {type(connection).__name__}. Store it on "
                    "the controller that uses it, e.g. self.connection = connection."
                )

    async def _watch(self, connection: Connection) -> None:
        """Each time a connection is marked down, mark its controllers down, then
        reconnect it and mark them up, or give up and leave them down."""
        while True:
            await connection.state.wait_down()
            await self._mark_down(connection)
            if not await self._reconnect(connection):
                logger.error("Giving up reconnecting", connection=connection)
                return

            await self._mark_up(connection)

    async def _mark_down(self, connection: Connection) -> None:
        """Pause the scans of the controllers holding a connection, and mark them
        disconnected."""
        for controller in self._controllers_by_connection[id(connection)]:
            self._scan_gates[id(controller)].clear()
            await controller.connected.update(False)

    async def _reconnect(self, connection: Connection) -> bool:
        """Try to reconnect, once its dependencies are up.

        Returns:
            Whether it reconnected within ``reconnect_attempts``

        """
        for attempt in range(connection.reconnect_attempts):
            if attempt:
                await asyncio.sleep(connection.reconnect_period)

            for dependency in self._dependencies[id(connection)]:
                await dependency.state.wait_up()

            try:
                await connection.close()
                await connection.connect()
            except Exception:
                logger.exception(
                    "Reconnect failed", connection=connection, attempt=attempt + 1
                )
            else:
                return True

        return False

    async def _mark_up(self, connection: Connection) -> None:
        """Mark a connection up, then mark connected each controller holding it,
        re-run its read-once reads and resume its scans."""
        connection.state.mark_up()
        for controller in self._controllers_by_connection[id(connection)]:
            await controller.connected.update(True)
            for read in self._initial_coros[id(controller)]:
                try:
                    await read()
                except Exception:
                    logger.exception("Exception in read-once read after reconnect")

            self._scan_gates[id(controller)].set()

    async def _close_connections(self) -> None:
        """Close the open connections, in reverse order."""
        while self._connections:
            connection = self._connections.pop()
            try:
                await connection.close()
            except Exception:
                logger.exception("Exception closing connection", connection=connection)

    def _collect_scans(self, controller: Controller) -> None:
        """Find the initial reads and periodic scans in a controller's tree.

        Each controller in the tree gets its own scan gate, and one periodic
        coroutine per period that waits on that gate before each cycle.

        """
        for sub_controller in _walk(controller):
            scans_by_period: dict[float, list[ScanCallback]] = defaultdict(list)
            for period, scan in _scans(sub_controller):
                if period is ONCE:
                    self._initial_coros[id(sub_controller)].append(scan)
                else:
                    scans_by_period[period].append(scan)

            gate = asyncio.Event()
            gate.set()
            self._scan_gates[id(sub_controller)] = gate
            self._scan_coros.extend(
                _periodic_scan_coro(period, scans, gate)
                for period, scans in scans_by_period.items()
            )

    def _cancel_tasks(self) -> None:
        for task in [*self._tasks, *self._watchers.values()]:
            if not task.done():
                task.cancel()

        self._tasks.clear()
        self._watchers.clear()

    def __del__(self):
        self._cancel_tasks()


def _walk(controller: BaseController) -> Iterator[BaseController]:
    """A controller and every controller below it, parents first"""
    yield controller
    for sub_controller in controller.sub_controllers.values():
        yield from _walk(sub_controller)


def _scans(controller: BaseController) -> Iterator[tuple[float, ScanCallback]]:
    """The period and callback of each scan method and polled attribute of a
    controller, not including its sub controllers"""
    for method in controller.scan_methods.values():
        yield method.period, method.fn

    for attribute in controller.attributes.values():
        if isinstance(attribute, AttrR) and attribute.has_io_ref():
            period = attribute.io_ref.update_period
            if period is not None:
                yield period, attribute.bind_update_callback()


def _held_connections(controller: BaseController) -> list[Connection]:
    """The connections stored as attributes of a controller, once each"""
    values = vars(controller).values()
    return list({id(v): v for v in values if isinstance(v, Connection)}.values())


def _check_connections_not_shared(specs: list[ControllerSpec]) -> None:
    """Check that no connection is used by more than one spec.

    Raises:
        ValueError: If a connection is shared between specs

    """
    owners: dict[int, str] = {}
    for spec in specs:
        for connection in {id(c): c for c in spec.connections}.values():
            owner = owners.setdefault(id(connection), spec.id)
            if owner != spec.id:
                raise ValueError(
                    f"{type(connection).__name__} is used by controllers "
                    f"'{owner}' and '{spec.id}'; a connection can't be shared "
                    "between controllers. Put them in one controller tree instead."
                )


def _dependency_types(connection: Connection) -> tuple[type[Connection], ...]:
    """A connection's ``depends_on`` as a tuple"""
    depends_on = connection.depends_on
    if isinstance(depends_on, type):
        return (depends_on,)

    return tuple(depends_on)


def _dependencies(
    connection: Connection, connections: list[Connection]
) -> list[Connection]:
    """The connections, from the same spec, that a connection depends on"""
    types = _dependency_types(connection)
    unique = {id(c): c for c in connections}.values()
    return [c for c in unique if c is not connection and isinstance(c, types)]


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
        for dependency in _dependencies(connection, unique):
            visit(dependency)
        visiting.pop()
        ordered.append(connection)

    for connection in unique:
        visit(connection)

    return ordered


def _periodic_scan_coro(
    period: float, scans: Sequence[ScanCallback], gate: asyncio.Event
) -> ScanCallback:
    """Create a coroutine to run scans at a given period

    Args:
        period: The period to run the scans at
        scans: A list of `ScanCallback` to run periodically
        gate: Waited on before each cycle, so clearing it pauses the scans

    Returns:
        A wrapper `ScanCallback` that runs all of the callbacks at a given period

    """

    async def scan_coro() -> None:
        while True:
            await gate.wait()
            try:
                await asyncio.gather(asyncio.sleep(period), *[scan() for scan in scans])
            except Exception:
                logger.exception("Exception in scan task", period=period)
                await asyncio.sleep(1)

    return scan_coro
