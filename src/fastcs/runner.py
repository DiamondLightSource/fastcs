import asyncio
import inspect
from collections import defaultdict
from collections.abc import Iterator, Sequence
from typing import Self, TypeVar

from fastcs.attributes.attr_r import AttrR
from fastcs.connections import Connection, ConnectionState, Introspection
from fastcs.connections.connection import _reconnecting
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.controllers.controller_spec import ControllerSpec
from fastcs.exceptions import IntrospectionMismatchError, ReconnectFailedError
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE

T = TypeVar("T")


class ControllerRunner:
    """Runs the lifecycle of one or more top-level controllers."""

    def __init__(self) -> None:
        self._connections: list[Connection] = []
        self._controllers: list[Controller] = []
        self._controllers_by_connection: dict[int, list[BaseController]] = defaultdict(
            list
        )

        self._dependencies: dict[int, list[Connection]] = {}
        self._names: dict[int, str] = {}

        self._controller_apis: list[ControllerAPI] = []
        self._initial_coros: dict[int, list[ScanCallback]] = defaultdict(list)
        self._scan_coros: list[ScanCallback] = []
        self._scan_gates: dict[int, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()
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
                runner._names.update(_connection_names(spec))
                for connection in _dependency_order(spec.connections):
                    connection.state = ConnectionState()
                    await connection.connect()
                    runner._connections.append(connection)
                    connection.introspection = await _introspect(connection)
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

    async def wait_for_fatal_error(self) -> None:
        """Wait on the connection watchers, which only stop by failing.

        Raises:
            ReconnectFailedError: If a connection doesn't reconnect
            IntrospectionMismatchError: If a connection's introspection changes
                structure on reconnect

        """
        await asyncio.gather(*self._watchers.values())

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
        reconnect it and mark them up."""
        while True:
            await connection.state.wait_down()
            await self._mark_controllers_down(connection)
            await self._reconnect(connection)
            await self._mark_controllers_up(connection)

    async def _mark_controllers_down(self, connection: Connection) -> None:
        """Pause the scans of the controllers holding a connection, and mark them
        disconnected."""
        for controller in self._controllers_by_connection[id(connection)]:
            self._scan_gates[id(controller)].clear()
            await controller.connected.update(False)

    async def _reconnect(self, connection: Connection) -> None:
        """Try to reconnect and introspect, once its dependencies are up, and mark the
        connection up if the introspection's structure is unchanged.

        Raises:
            ReconnectFailedError: If reconnecting is disabled, it has no
                ``reconnect_period``, or it doesn't reconnect within
                ``reconnect_attempts``
            IntrospectionMismatchError: If the new introspection's structure is
                different

        """
        name = self._names[id(connection)]
        attempts = connection.reconnect_attempts
        period = connection.reconnect_period
        if not attempts:
            raise ReconnectFailedError(
                f"{name} went down, and reconnecting is disabled"
            )
        if period is None:
            raise ReconnectFailedError(
                f"{name} can't reconnect, as it has reconnect_attempts but no "
                "reconnect_period"
            )

        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(period)

            for dependency in self._dependencies[id(connection)]:
                await dependency.state.wait_up()

            token = _reconnecting.set(connection)
            try:
                await connection.close()
                await connection.connect()
                introspection = await _introspect(connection)
            except Exception:
                logger.exception(
                    "Reconnect failed", connection=name, attempt=attempt + 1
                )
                continue
            finally:
                _reconnecting.reset(token)

            if connection.introspection_changed(introspection):
                raise IntrospectionMismatchError(
                    f"{name} introspection changed structure on reconnect, so the "
                    "controllers built from it are out of date"
                )

            connection.state.mark_up()
            return

        raise ReconnectFailedError(
            f"{name} went down and didn't reconnect within {attempts} attempts"
        )

    async def _mark_controllers_up(self, connection: Connection) -> None:
        """Mark connected each controller holding a connection, re-run its read-once
        reads and resume its scans."""
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


async def _introspect(connection: Connection[T]) -> T:
    """Introspect a connection, returning the value of its `Introspection`.

    Raises:
        TypeError: If ``introspect`` doesn't return an `Introspection`

    """
    introspection = await connection.introspect()
    if not isinstance(introspection, Introspection):
        raise TypeError(
            f"{type(connection).__name__}.introspect returned "
            f"{type(introspection).__name__}, not an Introspection"
        )

    return introspection.value


def _connection_names(spec: ControllerSpec) -> dict[int, str]:
    """Name each connection in a spec after the controller argument it's passed as,
    or after its class if it's passed into ``*args``"""
    names = {id(c): type(c).__name__ for c in spec.connections}
    positional = inspect.signature(spec.controller_class).bind_partial(*spec.args)
    arguments = {**positional.arguments, **spec.kwargs}
    for name, value in arguments.items():
        if isinstance(value, Connection):
            names[id(value)] = name

    return names


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
                    "between sister controllers. Put them in one controller tree "
                    "instead."
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
