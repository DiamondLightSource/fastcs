from collections.abc import Callable
from typing import Generic, ParamSpec, Self

from typing_extensions import TypeVar

from fastcs.connections.connection import Connection

P = ParamSpec("P")
ConnectionT = TypeVar("ConnectionT", bound=Connection)


class ConnectionSpec(Generic[P, ConnectionT]):
    """A deferred call that constructs a named connection.

    Example::

        # MetaConnection(port=5000), reconnecting 5 times a second apart
        ConnectionSpec("meta", MetaConnection, port=5000).reconnect(
            attempts=5, period=1.0
        )

    Args:
        name: The name of the connection, which is the controller argument it fills
        connection_class: The connection class, or any callable returning one
        args: Positional arguments for ``connection_class``
        kwargs: Keyword arguments for ``connection_class``

    """

    def __init__(
        self,
        name: str,
        connection_class: Callable[P, ConnectionT],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        self.name = name
        self.connection_class = connection_class
        self.args = args
        self.kwargs = kwargs
        self._reconnect: tuple[int | None, float | None] | None = None

    def reconnect(self, *, attempts: int | None, period: float | None) -> Self:
        """Set the connection's ``reconnect_attempts`` and ``reconnect_period``,
        instead of using its class's.

        Args:
            attempts: How many times to try to reconnect. None or 0 means don't
            period: How many seconds apart to try

        Returns:
            This spec

        Raises:
            ValueError: If ``attempts`` is set without ``period``

        """
        if attempts and period is None:
            raise ValueError(
                f"{self.name} sets reconnect attempts but not reconnect period"
            )

        self._reconnect = (attempts, period)
        return self

    def construct(self) -> ConnectionT:
        """Construct the connection, with any reconnect settings from `reconnect`"""
        connection = self.connection_class(*self.args, **self.kwargs)
        if self._reconnect is not None:
            attempts, period = self._reconnect
            connection.reconnect_attempts = attempts
            connection.reconnect_period = period

        return connection
