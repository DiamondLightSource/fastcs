from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_RECONNECT_PERIOD = 1.0
"""Seconds a connection waits between reconnect attempts, unless it says otherwise."""

DEFAULT_MAX_ATTEMPTS = 10
"""Reconnect attempts a connection makes before giving up, unless it says otherwise."""


class Connection(ABC, Generic[T]):
    """A link to hardware. Owns its own health state.

    Several controllers may share one instance - a sub controller that talks to the
    same device as its parent holds the same object rather than consulting the parent.
    Failure, gating and recovery all resolve through that shared object, so a tree of
    controllers behind one socket has one health state, one reconnect task and one
    retry budget between them.

    A concrete connection opens the link in `connect` and closes it in `close`, and
    calls `set_disconnected` from its own IO when the *transport* fails. That is the
    one place that can tell "the socket died" from "the device rejected that
    parameter", and only the first is a connection failure::

        async def get(self, path: str):
            try:
                response = await self._client.get(path)
            except (ConnectError, ReadTimeout):
                self.set_disconnected()  # transport is gone
                raise
            response.raise_for_status()  # a device complaint, not a dead link
            return response.json()["value"]

    Nothing above a connection has to catch anything, and no exception type is a
    contract between layers.

    The `ControllerRunner` keys its per-connection state by identity, so a
    ``Connection`` must never define ``__eq__``: two sockets with matching settings
    are two connections, and an ``__eq__`` would silently collapse them.

    Args:
        depends_on: A connection this one is layered over, if any. Declared, never
            derived - the runner will not attempt this one while that one is down.
        reconnect_period: Seconds between reconnect attempts. Defaults to the class
            attribute of the same name.
        max_attempts: Consecutive failed attempts before this connection gives up.
            Defaults to the class attribute of the same name.

    """

    # Class defaults. Framework defaults below, class attributes on a concrete
    # connection, constructor arguments on top - three tiers, each overriding the last.
    reconnect_period: float = DEFAULT_RECONNECT_PERIOD
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def __init__(
        self,
        depends_on: Connection | None = None,
        reconnect_period: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._connected = False
        self._up = asyncio.Event()
        self._down = asyncio.Event()
        self._down.set()

        # Declared, never derived. A connection layered over another names it here;
        # the runner will not attempt this one while that one is down.
        self.depends_on = depends_on

        if reconnect_period is not None:
            self.reconnect_period = reconnect_period
        if max_attempts is not None:
            self.max_attempts = max_attempts

    @property
    def connected(self) -> bool:
        """Whether the link is currently believed to be usable.

        Set by the framework - a driver never writes it. `connect` returning cleanly
        marks it up; `set_disconnected` from the connection's own IO marks it down.
        """
        return self._connected

    @abstractmethod
    async def connect(self) -> T:
        """Open the link, or raise. Return whatever introspection the caller needs.

        This means "make the link usable", not merely "open the socket" - a device
        that needs a mode set before it can be introspected has that write here,
        rather than in a controller's ``build``.

        The framework marks the connection connected when this returns cleanly, and
        compares the return value against the startup value on every reconnect.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the link. Called at shutdown and before every reconnect attempt.

        Must tolerate being called on a link that is already closed.
        """

    def set_disconnected(self) -> None:
        """Called by the connection's own IO when its transport fails.

        Wakes this connection's reconnect task and gates every scan that uses it.
        """
        self._connected = False
        self._up.clear()
        self._down.set()

    def _set_connected(self) -> None:
        """Framework only. Wakes anything awaiting this connection's recovery."""
        self._connected = True
        self._down.clear()
        self._up.set()

    async def wait_up(self) -> None:
        """Block until this connection is up. Returns immediately if it already is."""
        await self._up.wait()

    async def wait_down(self) -> None:
        """Block until this connection is down. Returns immediately if it already is."""
        await self._down.wait()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connected={self._connected})"
