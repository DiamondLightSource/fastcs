from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from fastcs.connections.policy import ConnectionPolicy


class Connection(ABC):
    """A link to hardware: a constructor, ``connect``, ``close`` and IO methods.

    A connection holds no health state and has no hooks. Its constructor arguments
    are its device settings - they become its block in ``fastcs.yaml`` - and its IO
    methods just talk to the device, raising when something goes wrong::

        class EigerConnection(Connection):
            def __init__(self, port: int) -> None:
                self._port = port

            async def connect(self) -> None: ...
            async def close(self) -> None: ...

            async def get(self, path: str) -> Any: ...

    Everything ongoing about the link - whether it is up, reconnecting, the scans
    that use it - belongs to its `Supervisor`. A controller never holds the
    connection itself but a *handle*: a stand-in that looks like the connection and
    routes every call through the supervisor, which fails fast while the link is
    down and tells a dead link from a device complaint by the exception raised (see
    `ConnectionPolicy`). So IO methods need no ``try``/``except`` of their own.

    Calls a connection makes to its own methods do not pass through the handle, so
    each call from a controller is counted exactly once.

    The framework keys per-connection state by identity, so a ``Connection`` must
    never define ``__eq__``: two sockets with matching settings are two
    connections, and an ``__eq__`` would silently collapse them.
    """

    depends_on: ClassVar[Sequence[type[Connection]]] = ()
    """Connection types this one needs before it can work.

    Read as: wait for every connection of these types under the same top-level
    controller. Connections are opened in that order at startup, and a reconnect
    waits for them to be up - and stalls if one of them gives up. A driver fact,
    declared once here rather than in every deployment::

        class OdinConnection(HTTPConnection):
            depends_on = [EigerConnection]
    """

    policy: ClassVar[ConnectionPolicy] = ConnectionPolicy()
    """Which failures mean the link is gone, and what to do when it cannot recover.

    A class attribute, not a constructor argument: a constructor argument would
    appear in every connection's config schema. Policies are frozen, so the default
    instance is shared.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Open the link, or raise.

        This means "make the link usable", not merely "open the socket" - a device
        that needs a mode set every time it comes back has that write here, rather
        than in a controller's ``setup``, which runs once only.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the link. Called at shutdown and before every reconnect attempt.

        Must tolerate being called on a link that is already closed.
        """

    @property
    def label(self) -> str:
        """What to call this connection's device in a failure message.

        The device node or address where a connection knows one, and the class
        name otherwise. Distinct from the name its supervisor logs, which comes
        from config rather than from the device.
        """
        return type(self).__name__

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.label})"
