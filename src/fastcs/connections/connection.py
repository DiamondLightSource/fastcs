from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar, Generic

from typing_extensions import TypeVar

IntrospectionT = TypeVar("IntrospectionT", default=None)


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device, optionally returning an introspection from ``connect``.

    Example::

        class EigerConnection(Connection[EigerIntrospection]):
            async def connect(self) -> EigerIntrospection: ...
    """

    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()
    """Connection type(s) in the same `ControllerSpec` to open before this one.

    Example::

        class OdinConnection(Connection):
            depends_on = EigerConnection
    """

    _introspection: IntrospectionT

    @property
    def introspection(self) -> IntrospectionT:
        """What ``connect`` returned when the runner opened this connection.

        Raises:
            RuntimeError: If the runner hasn't opened this connection yet

        """
        try:
            return self._introspection
        except AttributeError:
            raise RuntimeError(
                f"{type(self).__name__} has no introspection until the runner "
                "has opened it"
            ) from None

    @abstractmethod
    async def connect(self) -> IntrospectionT:
        """Open the link, or raise.

        Returns:
            The introspection, or ``None`` if this connection doesn't introspect

        """

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""
