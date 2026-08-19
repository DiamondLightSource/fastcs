import enum
from collections.abc import Callable, Coroutine
from inspect import Parameter, Signature
from types import MethodType
from typing import TYPE_CHECKING, Any, Concatenate, Generic, ParamSpec, TypeVar

from fastcs.datatypes import DType
from fastcs.logging import logger
from fastcs.methods.method import Controller_T, Method

if TYPE_CHECKING:
    from fastcs.controllers import BaseController  # noqa: F401


P = ParamSpec("P")
"""The parameters a `Command` takes"""
T = TypeVar("T")
"""The value a `Command` returns"""

UnboundCommandCallback = Callable[
    Concatenate[Controller_T, P], Coroutine[None, None, T]
]
"""A Command callback that is unbound and must be called with a `Controller` instance"""
CommandCallback = Callable[P, Coroutine[None, None, T]]
"""A Command callback that is bound and can be called without `self`"""


COMMAND_DTYPES: tuple[type, ...] = (bool, int, float, str, enum.Enum)
"""The types a command argument or return value may have.

A subset of ``DType``: arrays and tables are deliberately left out. Serving them
would mean duplicating the array serialisation each transport already has for
attributes rather than sharing it, which ADR 0015 explicitly does not want, and
an array-valued command has an attribute-shaped alternative today.
"""


def _validate_datatype(annotation: Any) -> type[DType]:
    """Check that an annotation is a type a command can take or return.

    Args:
        annotation: The annotation of a command parameter or return value

    Returns:
        The annotation, once it is known to be a type a command can carry

    Raises:
        TypeError: If the annotation is missing, or is not one of
            `COMMAND_DTYPES`. The message describes the annotation alone -
            the caller catches it to say which argument or return value it
            came from.

    """
    if annotation is Signature.empty:
        raise TypeError(
            "has no type annotation. A command's argument and return types "
            "must be fully known"
        )

    if not (isinstance(annotation, type) and issubclass(annotation, COMMAND_DTYPES)):
        raise TypeError(
            f"has unsupported type {annotation!r}. Commands take and return "
            f"{', '.join(t.__name__ for t in COMMAND_DTYPES)}"
        )

    return annotation


def _validate_arguments(
    signature: Signature, fn: Callable, *, skip: int
) -> tuple[type[DType], ...]:
    """Check a command's parameters and collect their types.

    Args:
        signature: The command's signature
        fn: The wrapped function, to name it in errors
        skip: Leading parameters that are not command arguments - one for an
            unbound method, which still declares ``self``

    Returns:
        The type of each argument, in order

    Raises:
        TypeError: If a parameter is not a positional argument of a known type

    """
    parameters = list(signature.parameters.values())[skip:]

    argument_types = []
    for parameter in parameters:
        if parameter.kind is Parameter.KEYWORD_ONLY:
            raise TypeError(
                f"Command {fn.__qualname__} has keyword-only argument "
                f"'{parameter.name}'. Command arguments are positional; "
                "keyword arguments are not supported yet"
            )
        if parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
            raise TypeError(
                f"Command {fn.__qualname__} takes *args or **kwargs. A "
                "command's arguments must be fully known"
            )

        try:
            argument_types.append(_validate_datatype(parameter.annotation))
        except TypeError as error:
            raise TypeError(
                f"Argument '{parameter.name}' of command {fn.__qualname__} {error}"
            ) from error

    return tuple(argument_types)


def _validate_return(signature: Signature, fn: Callable) -> type[DType] | None:
    annotation = signature.return_annotation
    if annotation in (None, Signature.empty):
        return None

    try:
        return _validate_datatype(annotation)
    except TypeError as error:
        raise TypeError(f"Return value of command {fn.__qualname__} {error}") from error


class Command(Method["BaseController"], Generic[P, T]):
    """A `Controller` `Method` that performs a single action when called.

    A command may take positional arguments and return a value, both of known
    types - ``Command[[float], None]`` moves to a position, ``Command[[], None]``
    is the void case. What it takes and gives back is its ``signature``, which
    is what a transport reads to decide how - or whether - to serve it.

    This class contains a function that is bound to a specific `Controller` instance and
    is callable outside of the class context, without an explicit `self` parameter.
    Calling an instance of this class will call the bound `Controller` method.
    """

    def __init__(self, fn: CommandCallback[P, T], *, group: str | None = None):
        super().__init__(fn, group=group)

    def _validate(self, fn: CommandCallback[P, T]) -> None:
        super()._validate(fn)

        self._argument_types = _validate_arguments(self.signature, fn, skip=0)
        self._return_datatype = _validate_return(self.signature, fn)

    @property
    def argument_types(self) -> tuple[type[DType], ...]:
        """The type of each positional argument the command takes."""
        return self._argument_types

    @property
    def return_datatype(self) -> type[DType] | None:
        """The type the command returns, or ``None`` if it returns nothing."""
        return self._return_datatype

    @property
    def is_void(self) -> bool:
        """Whether the command takes no arguments and returns nothing.

        A void command can be served by any transport; a typed one needs a
        protocol that can carry a typed call.
        """
        return not self._argument_types and self._return_datatype is None

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        return await self.fn(*args, **kwargs)

    @property
    def fn(self) -> CommandCallback[P, T]:
        async def command(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await self._fn(*args, **kwargs)
            except Exception:
                logger.exception("Command failed", fn=self._fn)
                raise

        return command


class UnboundCommand(Method[Controller_T], Generic[Controller_T, P, T]):
    """A wrapper of an unbound `Controller` method to be bound into a `Command`.

    This generic class stores an unbound `Controller` method - effectively a function
    that takes an instance of a specific `Controller` type (`Controller_T`). Instances
    of this class can be added at `Controller` definition, either manually or with use
    of the `command` wrapper, to register the method to be included in the API of the
    `Controller`. When the `Controller` is instantiated, these instances will be bound
    to the instance, creating a `Command` instance.
    """

    def __init__(
        self,
        fn: UnboundCommandCallback[Controller_T, P, T],
        *,
        group: str | None = None,
    ) -> None:
        super().__init__(fn, group=group)

    def _validate(self, fn: UnboundCommandCallback[Controller_T, P, T]) -> None:
        super()._validate(fn)

        if not self.parameters:
            raise TypeError(f"Command {fn.__qualname__} must be a method, taking self")

        # The leading parameter is the ``Controller`` this is bound to, not an
        # argument of the command.
        _validate_arguments(self.signature, fn, skip=1)
        _validate_return(self.signature, fn)

    def bind(self, controller: Controller_T) -> Command[P, T]:
        return Command(MethodType(self.fn, controller), group=self.group)


def command(
    *, group: str | None = None
) -> Callable[
    [UnboundCommandCallback[Controller_T, P, T]],
    UnboundCommandCallback[Controller_T, P, T],
]:
    """Decorator to register a `Controller` method as a `Command`

    The `Command` will be passed to the transport layer to expose in the API

    Args:
        group: Group to display this command under in the transport layer

    """

    def wrapper(
        fn: UnboundCommandCallback[Controller_T, P, T],
    ) -> UnboundCommandCallback[Controller_T, P, T]:
        setattr(fn, "__unbound_command__", UnboundCommand(fn, group=group))  # noqa: B010

        return fn

    return wrapper
