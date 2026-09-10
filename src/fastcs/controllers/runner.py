import asyncio
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from fastcs.connections import Connection, Connections
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE

MAX_BUILD_PASSES = 32
"""Passes the build phase makes before deciding the tree is not settling.

A ``build`` that adds a sub controller whose ``build`` adds another needs one pass
per tier; a cap catches runaway construction rather than hanging.
"""


@dataclass
class _ReconnectState:
    """What the runner remembers about one connection."""

    attempts: int = 0
    """Consecutive failed attempts. Reset by a clean connection."""

    exhausted: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when this connection has given up. Terminal until the process restarts.

    An `asyncio.Event` rather than a flag because dependents await it: setting it
    releases anything waiting on this connection, so they stall loudly instead of
    hanging silently.
    """


class ControllerRunner:
    """Runs one or more `Controller` s, without serving them anywhere.

    This owns the whole controller lifecycle - opening connections, building and
    setting up the tree, running the initial and periodic tasks, reconnecting after a
    failure, and tidying up - and nothing about how the controllers are presented.
    `FastCS` uses it and adds transports on top; an embedded caller that only wants
    the controllers running can use it on its own::

        runner = ControllerRunner(controller, connections)
        await runner.start()
        ...
        await runner.stop()

    **The runner owns the order of the startup sequence.** Every connection is opened
    first, then the tree is walked calling ``build``, then ``setup`` runs across the
    whole built tree, then the tasks start. Controllers never call their own hooks to
    compensate for sequencing.

    Starting is in two halves, because anything serving the controllers needs their
    `ControllerAPI` before the first values are read: ``build`` opens the connections,
    builds the tree and returns the APIs, and ``start`` does the rest. Calling
    ``start`` on its own does both.

    **A failure anywhere in startup aborts.** A partly built tree means an
    application with a silently incomplete set of parameters, which is worse than no
    application at all, because clients connect successfully and never find what they
    are looking for. The orchestrator owns the retry.

    **Idempotency is the caller's responsibility.** Starting a running runner, or
    stopping a stopped one, is not defined.

    Args:
        controllers: The controller(s) to run. Accepts either a single
            ``Controller`` or a sequence of them.
        connections: The declared connections - one `Connections` registry, or one
            per top-level entry, since role names are local to an entry. Required,
            and the whole list: every connection is declared up front, so the runner
            never looks in the tree for one. A tree with no connections at all
            passes an empty registry.
        loop: Optional event loop to create the tasks in

    """

    def __init__(
        self,
        controllers: Controller | Sequence[Controller],
        connections: Connections | Sequence[Connections],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if isinstance(controllers, Controller):
            controllers = [controllers]
        self._controllers: list[Controller] = list(controllers)
        self._loop = loop
        if isinstance(connections, Connections):
            connections = [connections]
        self._registries: list[Connections] = list(connections)

        self._connections: list[Connection] = []
        self._state: dict[Connection, _ReconnectState] = {}

        self._controller_apis: list[ControllerAPI] = []
        self._scan_coros: list[ScanCallback] = []
        self._initial_coros: list[ScanCallback] = []
        self._tasks: set[asyncio.Task] = set()

        self.fatal_error: asyncio.Event = asyncio.Event()
        """Set when the runner has hit something it cannot carry on from.

        A background task cannot usefully raise - nothing is awaiting it - and an
        embedded FastCS must not call ``sys.exit``, so a fatal condition is reported
        here instead. `FastCS` awaits it and shuts down; an embedder can do the same,
        and read `fatal_reason` for what happened.

        Nothing in the framework sets this today: its one producer was the
        introspection mismatch on reconnect, which went with introspection itself.
        The channel is kept because the problem it solves - a background task that
        cannot raise - has not gone anywhere.
        """

        self.fatal_reason: BaseException | None = None
        """Why `fatal_error` was set, if it was."""

    @property
    def controller_apis(self) -> list[ControllerAPI]:
        """The API of each controller. Empty until ``build`` has run."""
        return self._controller_apis

    @property
    def connections(self) -> list[Connection]:
        """The connections this runner supervises, in the order it opens them."""
        return list(self._connections)

    async def build(self) -> list[ControllerAPI]:
        """Open every connection, build the controller tree and create the APIs.

        Runs before anything is set up or scanned, so that a transport can be wired
        to the APIs and catch the first readback.

        Returns:
            The API of each controller, in the order they were given

        """
        try:
            return await self._open_and_build()
        except BaseException:
            # Startup aborts, but the connections opened before the failure are
            # still open, and no task exists yet for a later ``stop`` to be called
            # to cancel - so nothing else would ever close them.
            await self._close_connections()
            raise

    async def _open_and_build(self) -> list[ControllerAPI]:
        self._connections = self._collect_connections()
        self._check_dependencies()
        # Only safe once the cycle check above has passed.
        self._connections = self._in_dependency_order(self._connections)

        for connection in self._connections:
            self._state[connection] = _ReconnectState()
            await connection.connect()
            connection._set_connected()  # noqa: SLF001

        await self._build_phase()

        for controller in self._controllers:
            # Every class-body declaration must have been provisioned by now: the
            # build walk is finished, so nothing else is going to fill one in.
            controller.check_filled()

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
        """Set the tree up and start its tasks.

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

        self._warn_about_unclaimed_connections()
        self._warn_about_unpolled_connections()

        for coro in self._initial_coros:
            await coro()

        loop = self._loop or asyncio.get_event_loop()
        self._tasks = {loop.create_task(coro()) for coro in self._scan_coros}
        self._tasks |= {
            loop.create_task(self._reconnect_loop(connection))
            for connection in self._connections
        }

    async def stop(self) -> None:
        """Stop the tasks and close every connection.

        Shutdown is a runner operation rather than an author hook: connections are
        closed in reverse declaration order, so anything layered over another is
        closed before what it rides on. ``setup`` is not undone - devices keep their
        last configured state.
        """
        self._cancel_tasks()
        await self._close_connections()

    async def _close_connections(self) -> None:
        for connection in reversed(self._connections):
            try:
                await connection.close()
            except Exception:
                logger.exception("Exception while closing connection")

    # Startup

    def _collect_connections(self) -> list[Connection]:
        """Every connection the runner supervises, in the order it opens them.

        The declared ones, in declaration order, and nothing else. They are known
        before any controller is constructed, which is what lets a ``build`` add a
        sub controller holding an already-open connection - and what makes the
        list exact: a connection created later could not have been opened up front,
        so there is nothing to find by walking the tree.
        """
        return [
            connection
            for registry in self._registries
            for connection in registry.values()
        ]

    def _check_dependencies(self) -> None:
        """``depends_on`` is declared, so it can name anything at all.

        A connection can name one the runner does not supervise, or two can name
        each other. Either leaves a connection waiting forever with nothing said,
        so both fail at startup instead.
        """
        for connection in self._connections:
            self._check_dependencies_of(connection, [connection])

    def _check_dependencies_of(
        self, connection: Connection, path: list[Connection]
    ) -> None:
        """Depth-first over one connection's dependencies, carrying the path.

        A connection may name several, so the walk branches; ``path`` is the chain
        that got here, which is both how a cycle is spotted and what names it.
        """
        for dependency in connection.depends_on:
            if not self._supervises(dependency):
                # It would never be opened, so it would sit at
                # ``connected is False`` forever and this connection would
                # never be attempted again.
                raise ValueError(
                    f"{type(connection).__name__} depends on a "
                    f"{type(dependency).__name__} the runner does not "
                    "supervise, so it would never be opened. Declare it "
                    "alongside the connection that depends on it."
                )
            if any(dependency is node for node in path):
                chain = " -> ".join(type(node).__name__ for node in path)
                raise ValueError(
                    f"Cycle in connection dependencies: {chain} -> "
                    f"{type(dependency).__name__}"
                )
            self._check_dependencies_of(dependency, [*path, dependency])

    @staticmethod
    def _in_dependency_order(connections: list[Connection]) -> list[Connection]:
        """Declaration order, except that a dependency comes before its dependent.

        The initial open is sequential, so a connection layered over another must
        not be opened first - and ``depends_on`` need not follow the order they were
        declared in. Shutdown walks this list backwards, which closes a dependent
        before what it rides on for the same reason.

        Assumes the dependency graph is acyclic - `_check_dependencies` has run.
        """
        ordered: list[Connection] = []

        def visit(connection: Connection) -> None:
            if any(connection is done for done in ordered):
                return
            for dependency in connection.depends_on:
                visit(dependency)
            ordered.append(connection)

        for connection in connections:
            visit(connection)
        return ordered

    def _supervises(self, connection: Connection) -> bool:
        """Whether this runner opened, and will reconnect, a connection."""
        return any(connection is known for known in self._connections)

    async def _build_phase(self) -> None:
        """Walk the tree top-down calling ``build``, to a fixpoint.

        A ``build`` may add sub controllers, which need building themselves, so the
        walk repeats over anything newly added until a pass adds nothing.
        """
        built: set[int] = set()

        for _ in range(MAX_BUILD_PASSES):
            pending = [c for c in self._walk_controllers() if id(c) not in built]
            if not pending:
                self._check_connections_are_known()
                return

            for controller in pending:
                built.add(id(controller))
                await controller.build()

        raise RuntimeError(
            f"Controller tree did not settle in {MAX_BUILD_PASSES} build passes. "
            "A `build` that adds a sub controller on every pass never finishes."
        )

    def _check_connections_are_known(self) -> None:
        """A connection the runner never opened would never be reconnected either."""
        for controller in self._walk_controllers():
            connection: Connection | None = controller.connection
            if connection is None or connection in self._state:
                continue

            raise self._unsupervised_connection_error(controller, connection)

    @staticmethod
    def _unsupervised_connection_error(
        controller: BaseController, connection: Connection
    ) -> RuntimeError:
        return RuntimeError(
            f"Controller {'.'.join(controller.path) or type(controller).__name__} "
            f"holds a {type(connection).__name__} the runner did not open. A "
            "connection created during `build` cannot be supervised - declare it "
            "up front and claim it from the `Connections` registry."
        )

    def _warn_about_unclaimed_connections(self) -> None:
        for registry in self._registries:
            for name in sorted(registry.unclaimed()):
                self._warn_unclaimed(name)

    @staticmethod
    def _warn_unclaimed(name: str) -> None:
        logger.warning(
            "Connection declared but never used. It will be opened and "
            "reconnected forever while doing nothing.",
            connection=name,
        )

    def _warn_about_unpolled_connections(self) -> None:
        """Nothing detects a connection failing unless something uses it regularly.

        Phrased as fact rather than fault: an all-on-demand device is a legitimate
        design, it just will not notice a failure until the next write.
        """
        polled: set[int] = set()
        for controller in self._walk_controllers():
            connection: Connection | None = controller.connection
            if connection is None:
                continue
            if self._has_polling(controller):
                polled.add(id(connection))

        for connection in self._connections:
            if id(connection) in polled:
                continue

            logger.warning(
                "Connection has no polled attribute or scan method among its "
                "controllers, so nothing will detect it failing until the next "
                "write. It will not reconnect automatically.",
                connection=self._name_of(connection),
            )

    @staticmethod
    def _has_polling(controller: BaseController) -> bool:
        from fastcs.attributes.attr_r import AttrR

        for method in controller.scan_methods.values():
            if method.period is not ONCE:
                return True

        for attribute in controller.attributes.values():
            if not (isinstance(attribute, AttrR) and attribute.has_getter()):
                continue
            if attribute.poll_period is not ONCE and attribute.poll_period is not None:
                return True

        return False

    # Failure and recovery

    async def _reconnect_loop(self, connection: Connection) -> None:
        """Keep one connection alive, at its own pace.

        One task per connection, idle until that connection actually goes down - a
        healthy connection costs nothing, and a detector that wants to retry every
        five seconds does not have to compromise with a writer that wants one.
        """
        state = self._state[connection]

        while True:
            await connection.wait_down()

            if state.exhausted.is_set():
                return

            # If anything we ride on is down, wait for it rather than attempting. No
            # attempt means no increment, so the retry budget freezes while waiting.
            # All of them must be up: a connection layered over two links is no more
            # usable with one of them than with neither.
            down = [
                dependency
                for dependency in connection.depends_on
                if not dependency.connected
            ]
            if down:
                logger.info(
                    "Waiting on dependencies",
                    connection=self._name_of(connection),
                    dependencies=[self._name_of(d) for d in down],
                )
                await self._await_dependencies(down)

                stalled = [d for d in down if not d.connected]
                if stalled:
                    # A dependency gave up. This connection cannot succeed, but it
                    # is not itself exhausted - it has spent nothing. Say so, then
                    # wait; only a restart will change anything.
                    logger.error(
                        "Stalled: dependency gave up",
                        connection=self._name_of(connection),
                        dependencies=[self._name_of(d) for d in stalled],
                    )
                    return

            await self._attempt(connection)

            if not connection.connected and not state.exhausted.is_set():
                await asyncio.sleep(connection.reconnect_period)

    async def _await_dependencies(self, dependencies: list[Connection]) -> None:
        """Block until every dependency is back, or any one of them gives up.

        Waiting on recovery alone would hang forever once a dependency exhausts, so
        both outcomes are awaited and whichever lands first wins. Recovery is *all*
        of them - a gather - while exhaustion is any single one, because one that has
        given up is enough to make this connection unusable.
        """

        async def all_up() -> None:
            await asyncio.gather(*(dependency.wait_up() for dependency in dependencies))

        recovered = asyncio.create_task(all_up())
        gave_up = [
            asyncio.create_task(self._state[dependency].exhausted.wait())
            for dependency in dependencies
        ]

        _, pending = await asyncio.wait(
            {recovered, *gave_up}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

    async def _attempt(self, connection: Connection) -> None:
        """One reconnect attempt.

        Owns retry accounting, and is the only place a connection is marked back up.
        """
        state = self._state[connection]
        state.attempts += 1

        try:
            await connection.close()  # tolerate an already-closed link
            await connection.connect()
        except Exception:
            logger.exception("Reconnect failed", connection=self._name_of(connection))
            if state.attempts >= connection.reconnect_attempts:
                # Terminal until the process restarts. Setting the event releases
                # anything waiting on this connection, so dependents stall loudly
                # instead of hanging silently.
                state.exhausted.set()
                logger.error(
                    "Giving up",
                    connection=self._name_of(connection),
                    attempts=state.attempts,
                    blocks=[
                        self._name_of(dependent)
                        for dependent in self._dependents_of(connection)
                    ],
                )
            return

        connection._set_connected()  # noqa: SLF001
        state.attempts = 0  # a clean connection restores the budget

    def fail(self, error: BaseException) -> None:
        """Report a condition the runner cannot carry on from.

        Raising here would be invisible - this runs in a background task with nothing
        awaiting it - and an embedded FastCS must not call ``sys.exit``, so the
        failure is recorded and whatever is running the runner decides what to do.
        """
        if self.fatal_reason is None:
            self.fatal_reason = error
        self.fatal_error.set()

    def _dependents_of(self, connection: Connection) -> list[Connection]:
        return [
            other
            for other in self._connections
            # identity: declared, not derived
            if any(dependency is connection for dependency in other.depends_on)
        ]

    # Helpers

    def _name_of(self, connection: Connection) -> str:
        """What to call a connection in a log line."""
        for registry in self._registries:
            name = registry.name_of(connection)
            if name is not None:
                return name
        return type(connection).__name__

    def _walk_controllers(self) -> Iterator[BaseController]:
        """Every controller in the tree, level order."""
        queue: deque[BaseController] = deque(self._controllers)
        while queue:
            controller = queue.popleft()
            yield controller
            queue.extend(controller.sub_controllers.values())

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
