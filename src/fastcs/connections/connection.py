from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar, Generic, cast

from typing_extensions import TypeVar

IntrospectionT = TypeVar("IntrospectionT", default=None)


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device, which can optionally introspect it.

    Example::

        class EigerConnection(Connection[EigerIntrospection]):
            async def introspect(self) -> EigerIntrospection: ...
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
        """What ``introspect`` returned when the runner opened this connection.

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
    async def connect(self) -> None:
        """Open the link, or raise."""

    async def introspect(self) -> IntrospectionT:
        """Describe the device. Called by the runner after ``connect``.

        Returns:
            The introspection, or ``None`` if this connection doesn't introspect

        """
        return cast(IntrospectionT, None)

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""
