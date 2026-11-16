import copy
from collections.abc import Callable
from typing import Generic, ParamSpec, Self

from typing_extensions import TypeVar

from fastcs.connections.connection import Connection

P = ParamSpec("P")
ConnectionT = TypeVar("ConnectionT", bound=Connection)
"""The `Connection` that a `ConnectionSpec` constructs"""


class ConnectionSpec(Generic[P, ConnectionT]):
    """A deferred call that constructs a named connection.

    Example::

        # MetaConnection(port=5000), to fill the controller argument "meta"
        ConnectionSpec("meta", MetaConnection, port=5000)

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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConnectionSpec):
            return NotImplemented

        return self._fields() == other._fields()

    def __repr__(self) -> str:
        class_name = getattr(self.connection_class, "__name__", self.connection_class)
        arguments = [
            repr(self.name),
            str(class_name),
            *(repr(arg) for arg in self.args),
            *(f"{key}={value!r}" for key, value in self.kwargs.items()),
        ]
        return f"ConnectionSpec({', '.join(arguments)})"

    def _fields(self) -> tuple:
        return (self.name, self.connection_class, self.args, self.kwargs)

    def with_parent(self, parent_name: str) -> Self:
        """A copy of this spec, with its name prefixed by that of the connection that
        discovered it.

        Example::

            spec.with_parent("odin").name  # "meta" -> "odin_meta"

        Args:
            parent_name: The name of the connection whose introspection returned this

        Returns:
            The renamed copy

        """
        spec = copy.copy(self)
        spec.name = f"{parent_name}_{self.name}"
        return spec

    def construct(self) -> ConnectionT:
        """Construct the connection"""
        return self.connection_class(*self.args, **self.kwargs)
