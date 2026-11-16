from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, ClassVar, Generic, cast, overload

from pydantic import BaseModel
from typing_extensions import TypeVar

IntrospectionT = TypeVar("IntrospectionT", default=None)
"""What a `Connection`'s ``introspect`` returns, or ``None`` if it doesn't introspect"""


class DisconnectedError(ConnectionError):
    """Raised when using a connection whose link isn't open"""


class ConnectionState:
    """Whether a connection's link is up, set by ``@disconnect_on`` and the framework.

    The framework gives each connection it opens its own live state. It starts up.
    """

    def __init__(self) -> None:
        self._up = asyncio.Event()
        self._up.set()
        self._down = asyncio.Event()

    @property
    def up(self) -> bool:
        """Whether the link is up"""
        return self._up.is_set()

    def mark_down(self) -> None:
        """Mark the link down, waking anything waiting for that"""
        self._up.clear()
        self._down.set()

    def mark_up(self) -> None:
        """Mark the link up, waking anything waiting for that"""
        self._down.clear()
        self._up.set()

    async def wait_down(self) -> None:
        """Wait until the link is marked down"""
        await self._down.wait()

    async def wait_up(self) -> None:
        """Wait until the link is marked up"""
        await self._up.wait()


class _InertConnectionState(ConnectionState):
    """The state of a connection the framework hasn't opened, which is always up"""

    def mark_down(self) -> None:
        pass


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device, which can optionally introspect it.

    Example::

        class EigerConnection(Connection[EigerIntrospection]):
            async def introspect(self) -> EigerIntrospection: ...
    """

    # Connection type(s) in the same ControllerSpec to open before this one
    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()

    # Replaced with a live state by the framework when it opens this connection
    state: ConnectionState = _InertConnectionState()

    # How many times, and how many seconds apart, the framework tries to reconnect.
    # None or 0 means don't reconnect and stop FastCS when the link goes down
    reconnect_attempts: int | None = None
    reconnect_period: float | None = None

    _introspection: IntrospectionT
    _introspection_structure: Any

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Check the reconnect settings of a connection class.

        Raises:
            TypeError: If ``reconnect_attempts`` is set without ``reconnect_period``

        """
        super().__init_subclass__(**kwargs)
        if cls.reconnect_attempts and cls.reconnect_period is None:
            raise TypeError(
                f"{cls.__name__} sets reconnect_attempts but not reconnect_period"
            )

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
        self._introspection_structure = _structure(value)

    def introspection_changed(self, introspection: IntrospectionT) -> bool:
        """Whether an introspection's fields, keys or list lengths differ from those
        of the one this connection was opened with"""
        return _structure(introspection) != self._introspection_structure

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


_MethodT = TypeVar("_MethodT", bound=Callable[..., Coroutine[Any, Any, Any]])

# The connection the framework is reconnecting in this task, which may use its own IO
_reconnecting: ContextVar[Connection | None] = ContextVar("_reconnecting", default=None)


@overload
def disconnect_on(method: _MethodT, /) -> _MethodT: ...


@overload
def disconnect_on(
    *errors: type[BaseException],
) -> Callable[[_MethodT], _MethodT]: ...


def disconnect_on(*errors: Any) -> Any:
    """Mark a connection IO method whose failure means the link has gone.

    If the method raises one of ``errors``, a `ConnectionError` or a
    `DisconnectedError`, the connection's ``state`` is marked down and the exception
    is re-raised. If it is called while the state is down, it raises
    `DisconnectedError` without calling the method.

    Example::

        class EigerConnection(Connection):
            @disconnect_on(TimeoutError)
            async def get(self, key: str) -> Any: ...

    Args:
        errors: Other exceptions that mean the link has gone

    Raises:
        TypeError: If the method isn't async, or an error isn't an exception class

    """
    if len(errors) == 1 and not isinstance(errors[0], type):
        # Used bare, as @disconnect_on, so the only argument is the method itself
        return disconnect_on()(errors[0])

    link_errors = (ConnectionError, *errors)

    def decorator(method: _MethodT) -> _MethodT:
        name = getattr(method, "__qualname__", repr(method))
        if not inspect.iscoroutinefunction(method):
            raise TypeError(f"@disconnect_on needs an async method, but {name} isn't")
        for error in errors:
            if not (isinstance(error, type) and issubclass(error, BaseException)):
                raise TypeError(
                    f"@disconnect_on on {name} takes exception classes, not {error!r}"
                )

        @functools.wraps(method)
        async def wrapper(self: Connection, *args: Any, **kwargs: Any) -> Any:
            if not self.state.up and _reconnecting.get() is not self:
                raise DisconnectedError(f"{type(self).__name__} is disconnected")

            try:
                return await method(self, *args, **kwargs)
            except link_errors:
                self.state.mark_down()
                raise

        return cast(_MethodT, wrapper)

    return decorator


def _structure(value: Any) -> Any:
    """The shape of a value: the fields, keys and lengths it's made of, but not its
    leaf values or their types"""
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        return {name: _structure(getattr(value, name)) for name in fields}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _structure(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {key: _structure(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_structure(item) for item in value]
    return None
