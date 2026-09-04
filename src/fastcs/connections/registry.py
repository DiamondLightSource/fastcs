from __future__ import annotations

from typing import TypeVar

from fastcs.connections.connection import Connection

Connection_T = TypeVar("Connection_T", bound=Connection)


class Connections:
    """The connections available to a controller tree, claimed by name.

    Built once - by the launcher from the ``connections:`` block, or by hand - and
    forwarded down the tree. A controller claims what it needs with `get` rather than
    receiving a bare connection object, so a controller's constructor signature does
    not change when something three tiers below it needs a new connection::

        class EigerController(Controller):
            def __init__(self, connections: Connections) -> None:
                super().__init__()
                self.add_sub_controller("DET", EigerDetectorController(connections))
                self.add_sub_controller("OD", OdinController(connections))

        class EigerDetectorController(Controller):
            def __init__(self, connections: Connections) -> None:
                self.connection = connections.get("eiger", EigerConnection)
                super().__init__()

    Args:
        connections: The declared connections, keyed by the name controllers claim
            them under. Iteration order is declaration order, which is the order the
            runner opens them in.

    """

    def __init__(self, connections: dict[str, Connection]) -> None:
        self._connections = dict(connections)
        self._claimed: set[str] = set()

    def get(self, name: str, expected: type[Connection_T]) -> Connection_T:
        """Claim a connection by name, asserting its type.

        Called from ``__init__``, so a bad name or type fails at construction -
        before anything is opened - rather than at the first IO.

        Args:
            name: The name the connection was declared under
            expected: The `Connection` subclass the caller intends to use

        Returns:
            The declared connection

        Raises:
            KeyError: If nothing was declared under that name
            TypeError: If what was declared is not an ``expected``

        """
        try:
            connection = self._connections[name]
        except KeyError:
            raise KeyError(
                f"No connection named {name!r}. Declared: {sorted(self._connections)}"
            ) from None

        if not isinstance(connection, expected):
            raise TypeError(
                f"Connection {name!r} is {type(connection).__name__}, "
                f"but {expected.__name__} was expected"
            )

        self._claimed.add(name)
        return connection

    def unclaimed(self) -> set[str]:
        """Names declared but never claimed.

        A config typo that would otherwise be opened and reconnected forever while
        doing nothing, so the runner warns about it at startup.
        """
        return set(self._connections) - self._claimed

    def name_of(self, connection: Connection) -> str | None:
        """The name a connection was declared under, by identity."""
        for name, declared in self._connections.items():
            if declared is connection:
                return name
        return None

    def values(self) -> list[Connection]:
        """The declared connections, in declaration order."""
        return list(self._connections.values())

    def __contains__(self, name: object) -> bool:
        return name in self._connections

    def __len__(self) -> int:
        return len(self._connections)

    def __repr__(self) -> str:
        return f"Connections({sorted(self._connections)})"
