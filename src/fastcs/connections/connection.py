from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextvars import ContextVar
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Self,
    Union,
    cast,
    get_args,
    get_origin,
    overload,
)

from pydantic import BaseModel
from typing_extensions import TypeVar

if TYPE_CHECKING:
    from fastcs.connections.connection_spec import ConnectionSpec

IntrospectionT = TypeVar("IntrospectionT", default=None)
"""What a `Connection`'s ``introspect`` returns, or ``None`` if it doesn't introspect"""


@dataclasses.dataclass
class Introspection(Generic[IntrospectionT]):
    """What a `Connection`'s ``introspect`` returns.

    Example::

        async def introspect(self) -> Introspection[OdinIntrospection]:
            info = OdinIntrospection(...)
            meta = ConnectionSpec("meta", MetaConnection, port=info.meta_port)
            return Introspection(info, subconnections=[meta])

    Args:
        value: The description of the device, available as
            ``connection.introspection``
        subconnections: Other connections found on the device. Each is opened and set
            as the attribute of the connection that discovered it of the same name,
            which its class declares.

    """

    value: IntrospectionT
    subconnections: list[ConnectionSpec] = dataclasses.field(default_factory=list)


class DisconnectedError(Exception):
    """Raised when using a connection whose link is down or closed"""


class ConnectionState:
    """Whether a connection's link is up, changed by ``@disconnect_on`` and the
    framework."""

    def __init__(self) -> None:
        self._up = asyncio.Event()
        self._up.set()
        self._down = asyncio.Event()
        self._timeouts = 0

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
        self._timeouts = 0
        self._down.clear()
        self._up.set()

    def count_timeout(self) -> int:
        """Count a timeout from a ``@disconnect_on`` method.

        Returns:
            How many timeouts there have been in a row

        """
        self._timeouts += 1
        return self._timeouts

    def reset_timeouts(self) -> None:
        """Start counting timeouts again, after a ``@disconnect_on`` method works"""
        self._timeouts = 0

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

    def count_timeout(self) -> int:
        # Shared by every unopened connection, so it doesn't count
        return 0


class Connection(ABC, Generic[IntrospectionT]):
    """A link to a device, which can optionally introspect it.

    Example::

        class EigerConnection(Connection[EigerIntrospection]):
            async def introspect(self) -> Introspection[EigerIntrospection]: ...
    """

    # Connection type(s) in the same ControllerSpec to open before this one
    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()

    # Replaced with a live state by the framework when it opens this connection
    state: ConnectionState = _InertConnectionState()

    # How many times, and how many seconds apart, the framework tries to reconnect
    # None or 0 means don't reconnect and stop FastCS when the link goes down
    reconnect_attempts: int | None = None
    reconnect_period: float | None = None

    # How many timeouts in a row mean the link has gone, from @disconnect_on methods
    # that list TimeoutError
    disconnect_after_timeouts: int = 1

    _introspection: IntrospectionT
    _introspection_structure: Any

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Check the reconnect settings of a connection class.

        Raises:
            TypeError: If ``reconnect_attempts`` is set without ``reconnect_period``,
                or ``disconnect_after_timeouts`` isn't an integer of at least 1

        """
        super().__init_subclass__(**kwargs)
        if cls.reconnect_attempts and cls.reconnect_period is None:
            raise TypeError(
                f"{cls.__name__} sets reconnect_attempts but not reconnect_period"
            )
        timeouts = cls.disconnect_after_timeouts
        if not isinstance(timeouts, int) or timeouts < 1:
            raise TypeError(
                f"{cls.__name__} sets disconnect_after_timeouts to {timeouts!r}, but "
                "it must be an integer of at least 1"
            )

    def set_reconnect(
        self,
        *,
        attempts: int | None,
        period: float | None,
        disconnect_after_timeouts: int | None = None,
    ) -> Self:
        """Set this connection's ``reconnect_attempts`` and ``reconnect_period``, and
        optionally its ``disconnect_after_timeouts``, instead of using its class's.

        Example::

            odin = OdinConnection(url).set_reconnect(
                attempts=10, period=2.0, disconnect_after_timeouts=3
            )

        Args:
            attempts: How many times to try to reconnect. None or 0 means don't
            period: How many seconds apart to try
            disconnect_after_timeouts: How many timeouts in a row mean the link has
                gone, or None to keep the class's

        Returns:
            This connection

        Raises:
            ValueError: If ``attempts`` is set without ``period``, or
                ``disconnect_after_timeouts`` is less than 1

        """
        if attempts and period is None:
            raise ValueError(
                f"{type(self).__name__} sets reconnect attempts but not reconnect "
                "period"
            )
        if disconnect_after_timeouts is not None and disconnect_after_timeouts < 1:
            raise ValueError(
                f"{type(self).__name__} sets disconnect_after_timeouts to "
                f"{disconnect_after_timeouts}, but it must be at least 1"
            )

        self.reconnect_attempts = attempts
        self.reconnect_period = period
        if disconnect_after_timeouts is not None:
            self.disconnect_after_timeouts = disconnect_after_timeouts

        return self

    @property
    def introspection(self) -> IntrospectionT:
        """The value of the `Introspection` that ``introspect`` returned when the
        framework opened this connection.

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

    async def introspect(self) -> Introspection[IntrospectionT]:
        """Describe the device. Called by the framework after ``connect``.

        Returns:
            The introspection, whose value is ``None`` if this connection doesn't
            introspect

        """
        return Introspection(cast(IntrospectionT, None))

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

    If the method raises a `DisconnectedError` or one of ``errors``, the
    connection's ``state`` is marked down and the exception is re-raised. If it is
    called while the state is down, it raises `DisconnectedError` without calling the
    method. Used bare, as ``@disconnect_on``, only `DisconnectedError` marks it down.

    If ``errors`` includes `TimeoutError`, directly or through a base class like
    `OSError`, a timeout only marks it down once there have been the connection's
    ``disconnect_after_timeouts`` in a row. A call that returns starts the count again.

    Example::

        class EigerConnection(Connection):
            disconnect_after_timeouts = 3

            @disconnect_on(ConnectionError, TimeoutError)
            async def get(self, key: str) -> Any: ...

    Args:
        errors: The exceptions, besides `DisconnectedError`, that mean the link has
            gone

    Raises:
        TypeError: If the method isn't async, or an error isn't an exception class

    """
    if len(errors) == 1 and not isinstance(errors[0], type):
        # Used bare, as @disconnect_on, so the only argument is the method itself
        return disconnect_on()(errors[0])

    link_errors = (DisconnectedError, *errors)
    counts_timeouts = issubclass(TimeoutError, link_errors)

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
                result = await method(self, *args, **kwargs)
            except TimeoutError:
                # Before link_errors, which may include it through a base class
                if counts_timeouts:
                    limit = self.disconnect_after_timeouts
                    if self.state.count_timeout() >= limit:
                        self.state.mark_down()
                raise
            except link_errors:
                self.state.mark_down()
                raise

            self.state.reset_timeouts()
            return result

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


def _connection_hint(hint: Any) -> tuple[type[Connection] | None, bool]:
    """The connection class a controller argument's hint names, if any, and whether
    it's optional, as in ``MetaConnection | None``"""
    optional = False
    if get_origin(hint) in (Union, UnionType):
        members = [member for member in get_args(hint) if member is not type(None)]
        if len(members) != 1:
            return None, False

        hint, optional = members[0], True

    # A parametrised hint like IPConnection[Introspection] is checked by its class
    hint = get_origin(hint) or hint
    if isinstance(hint, type) and issubclass(hint, Connection):
        return hint, optional

    return None, False


def _declared_subconnections(
    connection_class: type[Connection],
) -> dict[str, tuple[type[Connection], bool]]:
    """The subconnections a connection class declares, as annotations hinted with a
    connection class, by name, with that class and whether it's optional"""
    declared: dict[str, tuple[type[Connection], bool]] = {}
    for klass in reversed(connection_class.__mro__):
        module = vars(sys.modules[klass.__module__])
        for name, annotation in inspect.get_annotations(klass).items():
            if isinstance(annotation, str):
                try:
                    annotation = eval(annotation, module, dict(vars(klass)))
                except NameError:
                    # Only imported for type checking, so not a subconnection
                    continue
            hinted, optional = _connection_hint(annotation)
            if hinted is not None:
                declared[name] = (hinted, optional)
            else:
                declared.pop(name, None)

    return declared
