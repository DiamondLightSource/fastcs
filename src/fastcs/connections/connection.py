from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence

from fastcs.connections.recovery import Recovery

DEFAULT_RECONNECT_PERIOD = 1.0
"""Seconds a connection waits between reconnect attempts, unless it says otherwise."""

DEFAULT_RECONNECT_ATTEMPTS = 10
"""Reconnect attempts a connection makes before giving up, unless it says otherwise."""


def normalise_depends_on(
    depends_on: Connection | Sequence[Connection] | None,
) -> list[Connection]:
    """``depends_on`` as a list, whether it was given as one, several or nothing."""
    if depends_on is None:
        return []
    if isinstance(depends_on, Connection):
        return [depends_on]
    return list(depends_on)


class Connection(ABC):
    """A link to hardware. Owns its own health state.

    Several controllers may share one instance - a sub controller that talks to the
    same device as its parent holds the same object rather than consulting the parent.
    Failure, gating and recovery all resolve through that shared object, so a tree of
    controllers behind one socket has one health state, one reconnect task and one
    retry budget between them.

    A concrete connection opens the link in ``connect`` and closes it in ``close``,
    and calls `set_disconnected` from its own IO when the *transport* fails. That is the
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
        depends_on: The connection(s) this one is layered over, if any - one, or a
            sequence of them. Declared, never derived: the runner will not attempt
            this one until *every* one of them is up, and stalls it if any gives up.
        reconnect_period: Seconds between reconnect attempts. Defaults to the class
            attribute of the same name.
        reconnect_attempts: Consecutive failed attempts before this connection gives
            up. Defaults to the class attribute of the same name.

    """

    # Class defaults. Framework defaults below, class attributes on a concrete
    # connection, constructor arguments on top - three tiers, each overriding the last.
    reconnect_period: float = DEFAULT_RECONNECT_PERIOD
    reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS

    recovery: Recovery = Recovery()
    """What to do when this connection fails; assign a policy to change it.

    A class attribute, not a constructor argument: a constructor argument would
    appear in every connection's config schema. Policies are stateless, so the
    default instance is shared.
    """

    def __init__(
        self,
        depends_on: Connection | Sequence[Connection] | None = None,
        reconnect_period: float | None = None,
        reconnect_attempts: int | None = None,
    ) -> None:
        self._connected = False
        self._up = asyncio.Event()
        self._down = asyncio.Event()
        self._down.set()

        # Declared, never derived. A connection layered over others names them here;
        # the runner will not attempt this one until all of them are up. Always a
        # list, so the runner has one shape to handle rather than three.
        self.depends_on: list[Connection] = normalise_depends_on(depends_on)

        if reconnect_period is not None:
            self.reconnect_period = reconnect_period
        if reconnect_attempts is not None:
            self.reconnect_attempts = reconnect_attempts

    @property
    def connected(self) -> bool:
        """Whether the link is currently believed to be usable.

        Set by the framework - a driver never writes it. ``connect`` returning cleanly
        marks it up; `set_disconnected` from the connection's own IO marks it down.
        """
        return self._connected

    @abstractmethod
    async def connect(self) -> None:
        """Open the link, or raise.

        This means "make the link usable", not merely "open the socket" - a device
        that needs a mode set before a driver can talk to it has that write here,
        rather than in a controller's ``build``.

        The framework marks the connection connected when this returns cleanly.
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

    @property
    def label(self) -> str:
        """What to call this connection's device in a failure message.

        The device node or address where a connection knows one, and the class
        name otherwise. Distinct from the role name the runner logs, which comes
        from config rather than from the device.
        """
        return type(self).__name__

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connected={self._connected})"
