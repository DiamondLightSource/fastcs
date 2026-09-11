"""What to do about a connection failure (ADR 0021).

A policy a connection holds, rather than a base class it inherits: the transport
and what to do when it fails are chosen separately, so one policy serves any
connection and a connection can be given any policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastcs.connections.connection import Connection


class Recovery:
    """Keep retrying. The default for every connection.

    Stateless, so one instance can be shared between any number of connections.
    """

    is_fatal: bool = False
    """Whether a terminal failure should bring the application down.

    A connection that has given up stalls its dependents and serves stale values
    forever. When only a restart can fix the cause, saying so and exiting is
    better than sitting there looking healthy.
    """

    def is_terminal(self, exc: BaseException) -> bool:
        """Whether a failed `Connection.connect` can never succeed in this process."""
        return False

    def reason(self, connection: Connection) -> str:
        """Why it cannot recover, for the log line that ends the retry loop."""
        return f"{connection.label} cannot recover from this failure."


class DRANode(Recovery):
    """A device node injected by a Kubernetes DRA claim.

    The claim is established when the pod starts, so a node that has gone will
    not reappear in it. Retrying cannot help and a pod restart can, so this is
    both terminal and fatal.
    """

    is_fatal = True

    def is_terminal(self, exc: BaseException) -> bool:
        return isinstance(exc, FileNotFoundError)

    def reason(self, connection: Connection) -> str:
        return (
            f"Device node {connection.label} has gone away. It comes from a "
            "Kubernetes DRA claim and will not reappear in this pod. Restart "
            "the pod to re-establish the claim."
        )
