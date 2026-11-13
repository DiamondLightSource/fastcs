from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar, Generic, cast

from typing_extensions import TypeVar

IntrospectionT = TypeVar("IntrospectionT", default=None)
"""What a `Connection`'s ``introspect`` returns, or ``None`` if it doesn't introspect"""


class DisconnectedError(ConnectionError):
    """Raised when using a connection whose link isn't open"""


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device, which can optionally introspect it.

    Example::

        class EigerConnection(Connection[EigerIntrospection]):
            async def introspect(self) -> EigerIntrospection: ...
    """

    # Connection type(s) in the same ControllerSpec to open before this one
    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()

    _introspection: IntrospectionT

    @property
    def introspection(self) -> IntrospectionT:
        """What ``introspect`` returned when the framework opened this connection.

        Raises:
            RuntimeError: If the framework hasn't opened this connection yet

        """
        try:
            return self._introspection
        except AttributeError:
            raise RuntimeError(
                f"{type(self).__name__} has no introspection until the framework "
                "has opened it"
            ) from None

    @introspection.setter
    def introspection(self, value: IntrospectionT) -> None:
        if hasattr(self, "_introspection"):
            raise RuntimeError(f"{type(self).__name__} introspection is already set")

        self._introspection = value

    @abstractmethod
    async def connect(self) -> None:
        """Open the link, or raise. Called by the framework before construction."""

    async def introspect(self) -> IntrospectionT:
        """Describe the device. Called by the framework after ``connect``.

        Returns:
            The introspection, or ``None`` if this connection doesn't introspect

        """
        return cast(IntrospectionT, None)

    @abstractmethod
    async def close(self) -> None:
        """Close the link. Called by the framework on stop, or if build fails."""
