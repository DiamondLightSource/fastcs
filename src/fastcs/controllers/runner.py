import asyncio
from collections import deque
from collections.abc import Iterator, Sequence

from fastcs.attributes import AttrR
from fastcs.connections import Connection, Supervisor
from fastcs.connections.supervisor import connection_of, supervisor_of
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.logging import logger
from fastcs.scheduling import ScanSchedule

MAX_BUILD_PASSES = 32
"""Passes the build phase makes before deciding the tree is not settling.

A ``build`` that adds a sub controller whose ``build`` adds another needs one pass
per tier; a cap catches runaway construction rather than hanging.
"""

CONNECTED_ATTRIBUTE = "connected"
"""The implicit attribute every controller gets at the seal.

Whether the controller can talk to its device: kept in step with its connection by
that connection's `Supervisor`, and always on for a controller with no connection.
An ordinary attribute, so every transport publishes it with no changes.
"""


class ControllerRunner:
    """Runs one or more `Controller` s, without serving them anywhere.

    The runner owns the startup and shutdown sequence, and nothing ongoing about a
    connection - that belongs to the connection's `Supervisor`. `FastCS` uses the
    runner and adds transports on top; an embedded caller that only wants the
    controllers running can use it on its own::

        eiger = Supervisor(EigerConnection(port=8000), name="eiger")
        runner = ControllerRunner(EigerController(eiger.handle), [eiger])
        await runner.start()
        ...
        await runner.stop()

    Startup is: open every connection, in dependency order; call ``build`` across
    the tree until it stops growing; seal the tree; then ``setup`` once, the
    read-once reads, and hand over to the supervisors. Controllers never call
    their own hooks to compensate for sequencing.

    Starting is in two halves, because anything serving the controllers needs their
    `ControllerAPI` before the first values are read: ``build`` opens the
    connections, builds and seals the tree and returns the APIs, and ``start`` does
    the rest. Calling ``start`` on its own does both.

    **A failure anywhere in startup aborts.** A partly built tree means an
    application with a silently incomplete set of parameters, which is worse than no
    application at all. The orchestrator owns the retry.

    Args:
        controllers: The top-level controller(s) to run
        supervisors: The supervisors of the connections the controllers were given.
            Either one sequence per top-level controller, or - for one controller, or
            when the controllers should share one scope - a single sequence. A
            connection's ``depends_on`` is resolved within its own scope.
        loop: Optional event loop to create the tasks in

    """

    def __init__(
        self,
        controllers: Controller | Sequence[Controller],
        supervisors: Sequence[Supervisor] | Sequence[Sequence[Supervisor]] = (),
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._supervisors: list[Supervisor] = []

        if isinstance(controllers, Controller):
            controllers = [controllers]
        self._controllers: list[Controller] = list(controllers)
        self._scopes = _scopes(supervisors, len(self._controllers))
        self._supervisors = [s for scope in self._scopes for s in scope]
        self._loop = loop

        self._opened: list[Supervisor] = []
        self._soft = ScanSchedule()
        self._controller_apis: list[ControllerAPI] = []

        self.fatal_error: asyncio.Event = asyncio.Event()
        """Set when something has happened that the application cannot carry on from.

        A background task cannot usefully raise - nothing is awaiting it - and an
        embedded FastCS must not call ``sys.exit``, so a fatal condition is reported
        here instead. `FastCS` awaits it and shuts down; an embedder can do the same,
        and read `fatal_reason` for what happened.

        Set by a supervisor whose reconnect fails terminally under a fatal
        `ConnectionPolicy` - a DRA device node that has gone away, say - since only
        a restart can fix that.
        """

        self.fatal_reason: BaseException | None = None
        """Why `fatal_error` was set, if it was."""

    @property
    def controller_apis(self) -> list[ControllerAPI]:
        """The API of each controller. Empty until ``build`` has run."""
        return self._controller_apis

    @property
    def supervisors(self) -> list[Supervisor]:
        """The supervisors this runner starts and stops, in the order it opens them."""
        return list(self._opened or self._supervisors)

    async def build(self) -> list[ControllerAPI]:
        """Open every connection, build and seal the tree, and create the APIs.

        Runs before anything is set up or scanned, so that a transport can be wired
        to the APIs and catch the first readback.

        Returns:
            The API of each controller, in the order they were given

        """
        try:
            return await self._open_build_and_seal()
        except BaseException:
            # Startup aborts, but the connections opened before the failure are
            # still open, and nothing else would ever close them.
            await self._close_connections()
            raise

    async def _open_build_and_seal(self) -> list[ControllerAPI]:
        dependencies = {
            supervisor: dependencies
            for scope in self._scopes
            for supervisor, dependencies in _resolve_dependencies(scope).items()
        }

        for supervisor in _in_dependency_order(self._supervisors, dependencies):
            self._opened.append(supervisor)
            await supervisor.open()

        await self._build_phase()

        # Every connection exists by now, so the graph is fixed from here on and
        # reconnect loops use it as it is.
        for supervisor, resolved in dependencies.items():
            supervisor.dependencies = resolved

        self._seal()

        self._controller_apis = [
            controller.create_api() for controller in self._controllers
        ]
        return self._controller_apis

    async def start(self) -> None:
        """Set the tree up and hand it over to the supervisors.

        Runs ``build`` first if it has not already run.
        """
        if not self._controller_apis:
            await self.build()

        try:
            await self._setup_and_run()
        except BaseException:
            # As in ``build``: a ``setup`` or an initial read that raises leaves
            # every connection open with nothing to close them.
            await self.stop()
            raise

    async def _setup_and_run(self) -> None:
        for controller in self._walk_controllers():
            await controller.setup()

        self._warn_about_unpolled_connections()

        await self._soft.set_connected(True)
        for supervisor in self._supervisors:
            await supervisor.schedule.set_connected(supervisor.up)

        await self._soft.read_once()
        for supervisor in self._supervisors:
            await supervisor.schedule.read_once()

        loop = self._loop or asyncio.get_event_loop()
        self._tasks = self._soft.start(loop, gate=None)
        for supervisor in self._supervisors:
            supervisor.start(loop, on_fatal=self.fail)

    async def stop(self) -> None:
        """Stop every task and close every connection.

        Connections close in reverse of the order they opened, so anything layered
        over another is closed before what it rides on. ``setup`` is not undone -
        devices keep their last configured state.
        """
        self._cancel_tasks()
        await self._close_connections()

    async def _close_connections(self) -> None:
        for supervisor in reversed(self._opened):
            try:
                await supervisor.close()
            except Exception:
                logger.exception(
                    "Exception while closing connection", connection=supervisor.name
                )

    def fail(self, error: BaseException) -> None:
        """Report a condition the application cannot carry on from.

        Raising here would be invisible - this is called from a background task with
        nothing awaiting it - and an embedded FastCS must not call ``sys.exit``, so
        the failure is recorded and whatever is running the runner decides what to
        do.
        """
        if self.fatal_reason is None:
            self.fatal_reason = error
        self.fatal_error.set()

    # Build and seal

    async def _build_phase(self) -> None:
        """Walk the tree top-down calling ``build``, to a fixpoint.

        A ``build`` may add sub controllers, which need building themselves, so the
        walk repeats over anything newly added until a pass adds nothing.
        """
        built: set[int] = set()

        for _ in range(MAX_BUILD_PASSES):
            pending = [c for c in self._walk_controllers() if id(c) not in built]
            if not pending:
                return

            for controller in pending:
                built.add(id(controller))
                await controller.build()

        raise RuntimeError(
            f"Controller tree did not settle in {MAX_BUILD_PASSES} build passes. "
            "A `build` that adds a sub controller on every pass never finishes."
        )

    def _seal(self) -> None:
        """Hand each controller's work to its owner, add ``connected``, and freeze.

        Every class-body declaration must have been provisioned by now: the build
        phase is over, so nothing else is going to fill one in.
        """
        for controller in self._controllers:
            controller.check_filled()

        held: set[int] = set()
        for controller in self._walk_controllers():
            supervisor = self._supervisor_of(controller)
            schedule = self._soft if supervisor is None else supervisor.schedule
            if supervisor is not None:
                held.add(id(supervisor))

            connected = AttrR(bool, description="Whether the device link is up")
            try:
                controller.add_attribute(CONNECTED_ATTRIBUTE, connected)
            except ValueError as exc:
                raise RuntimeError(
                    f"Controller {_describe(controller)} already has a member named "
                    f"{CONNECTED_ATTRIBUTE!r}, which the framework adds to every "
                    "controller. Rename it."
                ) from exc

            schedule.add_controller(controller)
            schedule.add_connected_attribute(connected)

        for supervisor in self._supervisors:
            if id(supervisor) not in held:
                logger.warning(
                    "Connection declared but not held by any controller. It will "
                    "be opened and reconnected while doing nothing.",
                    connection=supervisor.name,
                )

        for controller in self._controllers:
            controller.seal()

    def _supervisor_of(self, controller: BaseController) -> Supervisor | None:
        """The supervisor that owns a controller's work, or ``None`` if it is soft."""
        connection: Connection | None = controller.connection
        if connection is None:
            return None

        supervisor = supervisor_of(connection)
        if supervisor is not None and any(supervisor is s for s in self._supervisors):
            return supervisor

        # A test, or an embedder, may hand a controller the connection itself.
        # Nothing it calls passes the boundary, but its scans can still be paused.
        underlying = connection_of(connection)
        for supervisor in self._supervisors:
            if supervisor.connection is underlying:
                return supervisor

        raise RuntimeError(
            f"Controller {_describe(controller)} holds a "
            f"{type(underlying).__name__} the runner does not supervise, so it "
            "would never be opened or reconnected. Give it a handle from a "
            "`Supervisor` passed to the runner."
        )

    def _warn_about_unpolled_connections(self) -> None:
        """Nothing detects a connection failing unless something uses it regularly.

        Phrased as fact rather than fault: an all-on-demand device is a legitimate
        design, it just will not notice a failure until the next write.
        """
        for supervisor in self._supervisors:
            if not supervisor.schedule.has_polling:
                logger.warning(
                    "Connection has no polled attribute or scan method among its "
                    "controllers, so nothing will detect it failing until the next "
                    "write.",
                    connection=supervisor.name,
                )

    # Helpers

    def _walk_controllers(self) -> Iterator[BaseController]:
        """Every controller in the tree, level order."""
        queue: deque[BaseController] = deque(self._controllers)
        while queue:
            controller = queue.popleft()
            yield controller
            queue.extend(controller.sub_controllers.values())

    def _cancel_tasks(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

        for supervisor in self._supervisors:
            supervisor.stop()

    def __del__(self):
        self._cancel_tasks()


def _describe(controller: BaseController) -> str:
    return ".".join(controller.path) or type(controller).__name__


def _scopes(
    supervisors: Sequence[Supervisor] | Sequence[Sequence[Supervisor]],
    controllers: int,
) -> list[list[Supervisor]]:
    """The supervisors grouped by the top-level controller whose scope they are in."""
    flat = [each for each in supervisors if isinstance(each, Supervisor)]
    grouped = [list(each) for each in supervisors if not isinstance(each, Supervisor)]
    if flat and grouped:
        raise ValueError("Give the runner supervisors, or groups of them, not both.")

    if not grouped:
        scopes = [flat]
    elif len(grouped) == controllers:
        scopes = grouped
    else:
        raise ValueError(
            f"Given {len(grouped)} groups of supervisors for {controllers} "
            "controllers. Give one group per controller, or one group in all."
        )

    seen: set[int] = set()
    for supervisor in (s for scope in scopes for s in scope):
        if id(supervisor) in seen:
            raise ValueError(f"{supervisor} was given to the runner twice.")
        seen.add(id(supervisor))

    return scopes


def _resolve_dependencies(
    scope: list[Supervisor],
) -> dict[Supervisor, list[Supervisor]]:
    """Resolve each connection's ``depends_on`` types to the instances in its scope.

    Every connection of a named type is waited for, so where there are several of
    one type, a dependent waits for all of them.

    Raises:
        ValueError: If the resolved graph has a cycle, which would leave every
            connection in it waiting on the others forever

    """
    resolved = {
        supervisor: [
            other
            for other in scope
            if other is not supervisor
            and isinstance(other.connection, tuple(supervisor.connection.depends_on))
        ]
        for supervisor in scope
    }

    settled: set[int] = set()

    def visit(supervisor: Supervisor, path: list[Supervisor]) -> None:
        if id(supervisor) in settled:
            return
        if any(supervisor is node for node in path):
            cycle = " -> ".join(s.name for s in [*path, supervisor])
            raise ValueError(f"Cycle in connection dependencies: {cycle}")
        for dependency in resolved[supervisor]:
            visit(dependency, [*path, supervisor])
        settled.add(id(supervisor))

    for supervisor in scope:
        visit(supervisor, [])

    return resolved


def _in_dependency_order(
    supervisors: list[Supervisor], dependencies: dict[Supervisor, list[Supervisor]]
) -> list[Supervisor]:
    """Declaration order, except that a dependency comes before its dependent.

    Assumes the graph is acyclic - `_resolve_dependencies` has checked it.
    """
    ordered: list[Supervisor] = []

    def visit(supervisor: Supervisor) -> None:
        if any(supervisor is done for done in ordered):
            return
        for dependency in dependencies[supervisor]:
            visit(dependency)
        ordered.append(supervisor)

    for supervisor in supervisors:
        visit(supervisor)
    return ordered
