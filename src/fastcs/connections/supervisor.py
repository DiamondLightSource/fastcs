from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable, Coroutine
from typing import Any, Generic, TypeVar, cast

from fastcs.connections.connection import Connection
from fastcs.connections.policy import ConnectionPolicy, Failure
from fastcs.exceptions import DisconnectedError
from fastcs.logging import logger
from fastcs.scheduling import ScanSchedule

DEFAULT_RECONNECT_PERIOD = 1.0
"""Seconds a supervisor waits between reconnect attempts, unless configured."""

DEFAULT_RECONNECT_ATTEMPTS = 10
"""Reconnect attempts a supervisor makes before giving up, unless configured."""

Connection_T = TypeVar("Connection_T", bound=Connection)


class Supervisor(Generic[Connection_T]):
    """Owns everything ongoing about one connection.

    Its health, its reconnect loop and retry budget, the scans of the controllers
    that hold it, and their ``connected`` attributes. A controller is given the
    supervisor's `handle`, never the connection, so every call it makes passes the
    supervisor's boundary, which:

    - fails fast with `DisconnectedError` if the link is already down,
    - turns a link failure - see `ConnectionPolicy` - into `DisconnectedError`,
      marks the link down and wakes the reconnect loop,
    - passes a device error back to the caller unchanged.

    The launcher creates one per connection in ``fastcs.yaml``. An application that
    builds its own tree creates them itself, before the controllers that take
    their handles::

        eiger = Supervisor(EigerConnection(port=8000), name="eiger")
        controller = EigerController(eiger=eiger.handle)
        FastCS(controller, transports, supervisors=[eiger]).run()

    Args:
        connection: The connection to supervise. Nothing is opened yet.
        name: What to call it in log messages; the connection's class by default
        reconnect_attempts: Consecutive failed reconnect attempts before giving up
        reconnect_period: Seconds between reconnect attempts
        policy: How to read its failures; the connection class's ``policy`` by
            default

    """

    def __init__(
        self,
        connection: Connection_T,
        *,
        name: str | None = None,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_period: float = DEFAULT_RECONNECT_PERIOD,
        policy: ConnectionPolicy | None = None,
    ) -> None:
        self._connection = connection
        self.name = name or type(connection).__name__
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_period = reconnect_period
        self.policy = policy or type(connection).policy

        self._handle = cast(Connection_T, _Handle(self))

        self._up = False
        self._up_event = asyncio.Event()
        self._down_event = asyncio.Event()
        self._gave_up = asyncio.Event()
        self._timeouts = 0

        self.dependencies: list[Supervisor] = []
        """The supervisors this one waits for before reconnecting. Set at the seal."""

        self.schedule = ScanSchedule()
        """The work of the controllers holding this connection. Filled at the seal."""

        self._on_fatal: Callable[[BaseException], None] | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def connection(self) -> Connection_T:
        """The connection itself. Calls made on it bypass the boundary."""
        return self._connection

    @property
    def handle(self) -> Connection_T:
        """What a controller holds in place of the connection.

        Passes as the connection's own type, for type checkers and ``isinstance``
        alike, and routes every ``async`` method call through this supervisor.
        """
        return self._handle

    @property
    def up(self) -> bool:
        """Whether the link is believed to be usable."""
        return self._up

    @property
    def gave_up(self) -> bool:
        """Whether this supervisor has stopped trying. Only a restart helps now."""
        return self._gave_up.is_set()

    async def wait_up(self) -> None:
        """Block until the link is up. Returns immediately if it already is."""
        await self._up_event.wait()

    # The boundary

    async def call(
        self,
        method: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Call one of the connection's IO methods through the boundary."""
        if not self._up:
            raise DisconnectedError(f"{self.name} is disconnected")

        try:
            result = await method(*args, **kwargs)
        except Exception as exc:
            failure = self.policy.classify(exc)
            if failure is Failure.TIMEOUT:
                self._timeouts += 1
                if self._timeouts < self.policy.timeout_count:
                    raise
            elif failure is Failure.DEVICE:
                # The device answered, so the link is alive.
                self._timeouts = 0
                raise

            self.mark_down(exc)
            if isinstance(exc, DisconnectedError):
                raise
            raise DisconnectedError(f"{self.name} is disconnected: {exc!r}") from exc

        self._timeouts = 0
        return result

    def mark_down(self, error: BaseException) -> None:
        """Mark the link down and wake the reconnect loop. Idempotent."""
        if not self._up:
            return

        self._up = False
        self._timeouts = 0
        self._up_event.clear()
        self._down_event.set()
        logger.warning("Connection down", connection=self.name, error=repr(error))

    # Lifecycle

    async def open(self) -> None:
        """Open the link for the first time. Raises if it cannot be opened."""
        await self._connection.connect()
        self._set_up()

    async def close(self) -> None:
        """Close the link. Safe on a link that is already closed."""
        self._up = False
        self._up_event.clear()
        await self._connection.close()

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        on_fatal: Callable[[BaseException], None],
    ) -> None:
        """Start the scan loops and the reconnect loop."""
        self._on_fatal = on_fatal
        self._tasks = self.schedule.start(loop, self._up_event)
        self._tasks.add(loop.create_task(self._reconnect_loop()))

    def stop(self) -> None:
        """Cancel the scan loops and the reconnect loop."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

    def _set_up(self) -> None:
        self._up = True
        self._timeouts = 0
        self._down_event.clear()
        self._up_event.set()

    # Recovery

    async def _reconnect_loop(self) -> None:
        """Sleep until the link is marked down, then bring it back.

        One per connection, idle until that connection actually goes down, so a
        healthy connection costs nothing and each retries at its own pace.
        """
        while True:
            await self._down_event.wait()

            await self.schedule.set_connected(False)
            await self.schedule.flag_polled_invalid()

            if not await self._reconnect():
                return

    async def _reconnect(self) -> bool:
        """Retry until the link is back. False if it never will be."""
        attempts = 0
        while True:
            blocked = [d for d in self.dependencies if not d.up]
            if blocked:
                # Waiting is free: no attempt is made, so none is counted. A
                # connection layered over two links is no more usable with one of
                # them than with neither, so all of them must be up.
                logger.info(
                    "Waiting on dependencies",
                    connection=self.name,
                    dependencies=[d.name for d in blocked],
                )
                await _wait_for_all_up_or_any_gave_up(blocked)

                stalled = [d for d in blocked if d.gave_up]
                if stalled:
                    # This connection cannot succeed, but it has spent nothing
                    # either. Say so, then stop: only a restart helps now.
                    logger.error(
                        "Stalled: dependency gave up",
                        connection=self.name,
                        dependencies=[d.name for d in stalled],
                    )
                    return False
                continue

            try:
                await self._connection.close()  # tolerates an already-closed link
                await self._connection.connect()
            except Exception as exc:
                attempts += 1
                terminal = self.policy.is_terminal(exc)
                if terminal or attempts >= self.reconnect_attempts:
                    self._give_up(exc, attempts, terminal)
                    return False

                if attempts == 1:
                    logger.opt(exception=exc).warning(
                        "Reconnect failed, retrying",
                        connection=self.name,
                        period=self.reconnect_period,
                        attempts=self.reconnect_attempts,
                    )
                await asyncio.sleep(self.reconnect_period)
                continue

            self._set_up()
            logger.info("Connection back up", connection=self.name)
            await self.schedule.set_connected(True)
            # A rebooted device can come back with different values, and a
            # read-once read would otherwise stay stale forever.
            await self.schedule.reread_once()
            return True

    def _give_up(self, exc: Exception, attempts: int, terminal: bool) -> None:
        # Terminal until the process restarts. Setting the event releases anything
        # waiting on this connection, so dependents stall loudly instead of hanging.
        self._gave_up.set()
        logger.opt(exception=exc).error(
            "Giving up",
            connection=self.name,
            attempts=attempts,
            reason=self.policy.reason(self._connection) if terminal else None,
        )
        if terminal and self.policy.fatal and self._on_fatal is not None:
            # Only a restart can fix it, so ask for one rather than sit there
            # looking healthy while serving stale values.
            self._on_fatal(exc)

    def __repr__(self) -> str:
        return f"Supervisor({self.name}, up={self._up})"


async def _wait_for_all_up_or_any_gave_up(supervisors: list[Supervisor]) -> None:
    """Block until every one is up, or any one of them gives up.

    Waiting on recovery alone would hang forever once one gives up, so both are
    awaited and whichever lands first wins.
    """

    async def all_up() -> None:
        await asyncio.gather(*(s.wait_up() for s in supervisors))

    waiters = {asyncio.ensure_future(all_up())}
    waiters |= {asyncio.ensure_future(s._gave_up.wait()) for s in supervisors}  # noqa: SLF001

    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


class _Handle:
    """A stand-in for a connection that routes every call through its supervisor.

    ``__class__`` reports the connection's own type, so ``isinstance`` checks pass
    and the handle can be passed anywhere the connection could. Only ``async``
    methods go through the boundary; anything else - a property, a sync helper,
    tracing - is the connection's own, unchanged.
    """

    __slots__ = ("_supervisor",)

    def __init__(self, supervisor: Supervisor) -> None:
        object.__setattr__(self, "_supervisor", supervisor)

    @property
    def __class__(self) -> type:  # pyright: ignore[reportIncompatibleMethodOverride]
        return type(self._supervisor.connection)

    def __getattr__(self, name: str) -> Any:
        supervisor: Supervisor = self._supervisor
        value = getattr(supervisor.connection, name)
        if not inspect.iscoroutinefunction(value):
            return value

        @functools.wraps(value)
        async def through_boundary(*args: Any, **kwargs: Any) -> Any:
            return await supervisor.call(value, *args, **kwargs)

        return through_boundary

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._supervisor.connection, name, value)

    def __dir__(self) -> list[str]:
        return dir(self._supervisor.connection)

    def __repr__(self) -> str:
        return f"<handle to {self._supervisor.connection!r}>"


def connection_of(connection: Connection) -> Connection:
    """The connection behind a handle, or the connection itself if it is not one.

    Two handles to one connection need not be the same object, so anything that
    compares connections compares what this returns.
    """
    if type(connection) is _Handle:
        return connection._supervisor.connection  # noqa: SLF001
    return connection


def supervisor_of(connection: Connection) -> Supervisor | None:
    """The supervisor behind a handle, or ``None`` for a bare connection."""
    if type(connection) is _Handle:
        return connection._supervisor  # noqa: SLF001
    return None
