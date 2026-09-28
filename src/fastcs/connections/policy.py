"""How a connection's failures are read, and what to do about them.

A policy a connection holds, rather than a base class it inherits: the transport
and what to do when it fails are chosen separately, so one policy serves any
connection and a connection can be given any policy.

A policy is a set of independent settings rather than one class per behaviour, so
that a slow DRA device is `DRAPolicy` with a different timeout count, not a
``DRASlowPolicy``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from fastcs.exceptions import DisconnectedError

if TYPE_CHECKING:
    from fastcs.connections.connection import Connection


DEFAULT_TIMEOUT_COUNT = 3
"""Consecutive timeouts that mean the link is gone, rather than the device slow."""

DEFAULT_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.TimeoutException,
)
"""A call that took too long. One is not a dead link; several in a row are."""

DEFAULT_DISCONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    # Connection reset, broken pipe, refused, unreachable, a vanished device node,
    # a serial port that went away - every one an OSError.
    OSError,
    # EOF, or a read cut short (asyncio.IncompleteReadError is an EOFError)
    EOFError,
    # Anything httpx could not complete at the transport level. Not an HTTP error
    # status: that is the device answering.
    httpx.TransportError,
)
"""What the link failing looks like, as opposed to the device saying no."""


class Failure(enum.Enum):
    """What an exception out of a connection's IO means."""

    DISCONNECTED = "disconnected"
    """The link is gone."""

    TIMEOUT = "timeout"
    """The call took too long. Disconnected once enough of them happen in a row."""

    DEVICE = "device"
    """The device answered, and said no. Passed back to the caller unchanged."""


@dataclass(frozen=True)
class ConnectionPolicy:
    """Keep retrying, and classify failures by the default rules.

    Assign one to a `Connection` class, or pass one to a `Supervisor`, to change
    how its failures are read or what happens when it cannot recover. Frozen, so
    one instance can be shared between any number of connections.
    """

    disconnection_errors: tuple[type[BaseException], ...] = DEFAULT_DISCONNECTION_ERRORS
    """Exceptions from connection IO that mean the link is gone."""

    timeout_errors: tuple[type[BaseException], ...] = DEFAULT_TIMEOUT_ERRORS
    """Exceptions from connection IO that mean the call took too long.

    Checked before `disconnection_errors`, since a `TimeoutError` is an `OSError`.
    """

    timeout_count: int = DEFAULT_TIMEOUT_COUNT
    """Consecutive timeouts that mark the link down."""

    terminal_errors: tuple[type[BaseException], ...] = ()
    """Failures of `Connection.connect` that can never succeed in this process.

    A reconnect that fails with one of these gives up at once, rather than
    spending the rest of its attempts on retries that cannot succeed.
    """

    fatal: bool = False
    """Whether a terminal failure should bring the application down.

    A connection that has given up stalls its dependents and serves stale values
    forever. When only a restart can fix the cause, saying so and exiting is
    better than sitting there looking healthy.
    """

    def classify(self, exc: BaseException) -> Failure:
        """What an exception raised by a connection's IO means."""
        if isinstance(exc, DisconnectedError):
            # Raised on purpose, by a driver that recognised its device going away.
            return Failure.DISCONNECTED
        if isinstance(exc, self.timeout_errors):
            return Failure.TIMEOUT
        if isinstance(exc, self.disconnection_errors):
            return Failure.DISCONNECTED
        return Failure.DEVICE

    def is_terminal(self, exc: BaseException) -> bool:
        """Whether a failed `Connection.connect` can never succeed in this process."""
        return isinstance(exc, self.terminal_errors)

    def reason(self, connection: Connection) -> str:
        """Why it cannot recover, for the log line that ends the retry loop."""
        return f"{connection.label} cannot recover from this failure."


@dataclass(frozen=True)
class DRAPolicy(ConnectionPolicy):
    """A device node injected by a Kubernetes DRA claim.

    The claim is established when the pod starts, so a node that has gone will
    not reappear in it. Retrying cannot help and a pod restart can, so a missing
    node is both terminal and fatal.
    """

    terminal_errors: tuple[type[BaseException], ...] = (FileNotFoundError,)
    fatal: bool = True

    def reason(self, connection: Connection) -> str:
        return (
            f"Device node {connection.label} has gone away. It comes from a "
            "Kubernetes DRA claim and will not reappear in this pod. Restart "
            "the pod to re-establish the claim."
        )
