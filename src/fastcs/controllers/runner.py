import asyncio
import inspect
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


class IntrospectionMismatchError(RuntimeError):
    """A device came back from a reconnect describing itself differently.

    ``build`` runs once, so there is no way to accommodate the new shape: the
    application has an attribute tree that no longer matches the hardware. The runner
    treats this as fatal - it stops, and `ControllerRunner.fatal_error` is set so
    whatever is running it can exit.
    """


@dataclass
class _ReconnectState:
    """What the runner remembers about one connection."""

    introspection: object = None
    """What ``connect`` returned at startup, compared against on every reconnect."""

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

        runner = ControllerRunner(controller, connections=connections)
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
        loop: Optional event loop to create the tasks in
        connections: The declared connections. When given, they are opened in
            declaration order before the tree is walked, so a ``build`` that adds sub
            controllers can hand them an already-open connection. When omitted, the
            runner collects the connections the tree already holds, by identity.

    """

    def __init__(
        self,
        controllers: Controller | Sequence[Controller],
        loop: asyncio.AbstractEventLoop | None = None,
        connections: Connections | None = None,
    ) -> None:
        if isinstance(controllers, Controller):
            controllers = [controllers]
        self._controllers: list[Controller] = list(controllers)
        self._loop = loop
        self._registry = connections

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

        for connection in self._connections:
            state = _ReconnectState()
            self._state[connection] = state
            state.introspection = await connection.connect()
            connection._set_connected()  # noqa: SLF001

        await self._build_phase()

        for controller in self._controllers:
            controller._validate_type_hints()  # noqa: SLF001

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

        From the registry when there is one - declaration order, and known before any
        controller is constructed, which is what lets a ``build`` add a sub controller
        holding an already-open connection. Otherwise from the tree, level order and
        deduplicated by identity: two sockets with matching settings are two
        connections, so identity rather than equality.
        """
        if self._registry is not None:
            return self._registry.values()

        seen: dict[int, Connection] = {}
        for controller in self._walk_controllers():
            connection: Connection | None = controller.connection
            if connection is not None and id(connection) not in seen:
                seen[id(connection)] = connection
        return list(seen.values())

    def _check_dependencies(self) -> None:
        """``depends_on`` is declared, so it can name anything at all.

        A connection can name one the runner does not supervise, or two can name
        each other. Either leaves a connection waiting forever with nothing said,
        so both fail at startup instead.
        """
        for connection in self._connections:
            seen = [connection]
            dependency = connection.depends_on
            while dependency is not None:
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
                if any(dependency is node for node in seen):
                    chain = " -> ".join(type(node).__name__ for node in seen)
                    raise ValueError(
                        f"Cycle in connection dependencies: {chain} -> "
                        f"{type(dependency).__name__}"
                    )
                seen.append(dependency)
                dependency = dependency.depends_on

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
                await self._call_build(controller)

        raise RuntimeError(
            f"Controller tree did not settle in {MAX_BUILD_PASSES} build passes. "
            "A `build` that adds a sub controller on every pass never finishes."
        )

    async def _call_build(self, controller: BaseController) -> None:
        """Call ``build``, passing the connection's introspection if it wants it.

        ``build(self)`` gets nothing and ``build(self, info)`` gets whatever this
        controller's connection returned from ``connect``.
        """
        wants_introspection = bool(inspect.signature(controller.build).parameters)

        if not wants_introspection:
            await controller.build()
            return

        connection: Connection | None = controller.connection
        if connection is None:
            raise TypeError(
                f"{type(controller).__name__}.build takes an introspection "
                "argument, but the controller has no connection to get one from."
            )

        state = self._state.get(connection)
        if state is None:
            # A controller added during ``build`` that holds an unopened
            # connection reaches here before the pass that would catch it, and a
            # bare KeyError would say nothing useful.
            raise self._unsupervised_connection_error(controller, connection)

        await controller.build(state.introspection)  # type: ignore[call-arg]

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
        if self._registry is None:
            return

        for name in sorted(self._registry.unclaimed()):
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

            # If what we ride on is down, wait for it rather than attempting. No
            # attempt means no increment, so the retry budget freezes while waiting.
            dependency = connection.depends_on
            if dependency is not None and not dependency.connected:
                logger.info(
                    "Waiting on dependency",
                    connection=self._name_of(connection),
                    dependency=self._name_of(dependency),
                )
                await self._await_dependency(dependency)

                if not dependency.connected:
                    # The dependency gave up. This connection cannot succeed, but it
                    # is not itself exhausted - it has spent nothing. Say so, then
                    # wait; only a restart will change anything.
                    logger.error(
                        "Stalled: dependency gave up",
                        connection=self._name_of(connection),
                        dependency=self._name_of(dependency),
                    )
                    return

            await self._attempt(connection)

            if not connection.connected and not state.exhausted.is_set():
                await asyncio.sleep(connection.reconnect_period)

    async def _await_dependency(self, dependency: Connection) -> None:
        """Block until the dependency either comes back or gives up.

        Waiting on recovery alone would hang forever once the dependency exhausts, so
        both outcomes are awaited and whichever lands first wins.
        """
        dependency_state = self._state[dependency]

        recovered = asyncio.create_task(dependency.wait_up())
        gave_up = asyncio.create_task(dependency_state.exhausted.wait())

        _, pending = await asyncio.wait(
            {recovered, gave_up}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

    async def _attempt(self, connection: Connection) -> None:
        """One reconnect attempt.

        Owns retry accounting and the introspection check, and is the only place a
        connection is marked back up.
        """
        state = self._state[connection]
        state.attempts += 1

        try:
            await connection.close()  # tolerate an already-closed link
            introspection = await connection.connect()
        except Exception:
            logger.exception("Reconnect failed", connection=self._name_of(connection))
            if state.attempts >= connection.max_attempts:
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

        try:
            differs = self._introspection_differs(introspection, state.introspection)
        except TypeError as error:
            # Raised for an introspection result that cannot be compared. Letting
            # it out of here would kill this reconnect task silently - nothing
            # awaits it - and every scan gated on this connection would then wait
            # in `wait_up` forever. Report it the same way a mismatch is reported.
            logger.exception(
                "Cannot compare introspection", connection=self._name_of(connection)
            )
            self._fail(error)
            return

        if differs:
            self._fatal_introspection_mismatch(
                connection, state.introspection, introspection
            )
            return

        connection._set_connected()  # noqa: SLF001
        state.attempts = 0  # a clean connection restores the budget

    @staticmethod
    def _introspection_differs(new: object, old: object) -> bool:
        """Whether a device is describing itself differently than it did at startup.

        ``!=`` is the comparison, which means an introspection result has to compare
        to a single bool - a dataclass does, an array of values does not. Saying so
        beats an ``ambiguous truth value`` escaping from a background task.
        """
        try:
            return bool(new != old)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "Introspection results are compared with `!=` on every reconnect, "
                f"but comparing {type(new).__name__} did not give a single bool. "
                "Return something that compares by value, such as a dataclass of "
                "plain fields."
            ) from exc

    def _fatal_introspection_mismatch(
        self, connection: Connection, expected: object, received: object
    ) -> None:
        error = IntrospectionMismatchError(
            f"Connection {self._name_of(connection)} came back describing itself "
            f"differently: expected {expected!r}, got {received!r}. `build` cannot "
            "run again, so the application cannot represent this device any more."
        )
        logger.error(
            "Introspection mismatch on reconnect",
            connection=self._name_of(connection),
            expected=repr(expected),
            received=repr(received),
        )
        self._fail(error)

    def _fail(self, error: BaseException) -> None:
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
            if other.depends_on is connection  # identity: declared, not derived
        ]

    # Helpers

    def _name_of(self, connection: Connection) -> str:
        """What to call a connection in a log line."""
        if self._registry is not None:
            name = self._registry.name_of(connection)
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
