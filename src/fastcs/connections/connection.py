from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar, Generic

from typing_extensions import TypeVar

IntrospectionT = TypeVar("IntrospectionT", default=None)


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device.

    A connection to a device that can describe itself returns that description from
    ``connect``, and declares its type as the type parameter::

        class EigerConnection(Connection[EigerIntrospection]):
            async def connect(self) -> EigerIntrospection: ...

    A plain ``Connection`` doesn't introspect, and its ``connect`` returns ``None``.
    """

    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()
    """The connection type, or types, this one needs before it can work.

    This connection depends on every connection of these types in the same
    `ControllerSpec`. The runner opens those first, and closes them last::

        class OdinConnection(Connection):
            depends_on = EigerConnection
    """

    _introspection: IntrospectionT

    @property
    def introspection(self) -> IntrospectionT:
        """What ``connect`` returned when the runner first opened this connection.

        Every connection is open before any controller is constructed, so a
        controller can use this in its ``__init__``.

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
            The device's description of itself, or ``None`` if this connection
            doesn't introspect

        """

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""
