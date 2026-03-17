from collections.abc import Callable, Coroutine
from types import MethodType
from typing import TYPE_CHECKING

from fastcs.logging import logger
from fastcs.methods.method import Controller_T, Method

if TYPE_CHECKING:
    from fastcs.controllers import BaseController  # noqa: F401


UnboundCommandCallback = Callable[[Controller_T], Coroutine[None, None, None]]
"""A Command callback that is unbound and must be called with a `Controller` instance"""
CommandCallback = Callable[[], Coroutine[None, None, None]]
"""A Command callback that is bound and can be called without `self`"""


class Command(Method["BaseController"]):
    """A `Controller` `Method` that performs a single action when called.

    This class contains a function that is bound to a specific `Controller` instance and
    is callable outside of the class context, without an explicit `self` parameter.
    Calling an instance of this class will call the bound `Controller` method.
    """

    def __init__(self, fn: CommandCallback, *, group: str | None = None):
        super().__init__(fn, group=group)

    def _validate(self, fn: CommandCallback) -> None:
        super()._validate(fn)

        if not len(self.parameters) == 0:
            raise TypeError(f"Command method cannot have arguments: {fn}")

    async def __call__(self):
        return await self.fn()

    @property
    def fn(self) -> CommandCallback:
        async def command():
            try:
                return await self._fn()
            except Exception:
                logger.exception("Command failed", fn=self._fn)
                raise

        return command


class UnboundCommand(Method[Controller_T]):
    """A wrapper of an unbound `Controller` method to be bound into a `Command`.

    This generic class stores an unbound `Controller` method - effectively a function
    that takes an instance of a specific `Controller` type (`Controller_T`). Instances
    of this class can be added at `Controller` definition, either manually or with use
    of the `command` wrapper, to register the method to be included in the API of the
    `Controller`. When the `Controller` is instantiated, these instances will be bound
    to the instance, creating a `Command` instance.
    """

    def __init__(
        self, fn: UnboundCommandCallback[Controller_T], *, group: str | None = None
    ) -> None:
        super().__init__(fn, group=group)

    def _validate(self, fn: UnboundCommandCallback[Controller_T]) -> None:
        super()._validate(fn)

        if not len(self.parameters) == 1:
            raise TypeError("Command method cannot have arguments")

    def bind(self, controller: Controller_T) -> Command:
        return Command(MethodType(self.fn, controller), group=self.group)


def command(
    *, group: str | None = None
) -> Callable[
    [UnboundCommandCallback[Controller_T]], UnboundCommandCallback[Controller_T]
]:
    """Decorator to register a `Controller` method as a `Command`

    The `Command` will be passed to the transport layer to expose in the API

    Args:
        group: Group to display this command under in the transport layer

    """

    def wrapper(
        fn: UnboundCommandCallback[Controller_T],
    ) -> UnboundCommandCallback[Controller_T]:
        setattr(fn, "__unbound_command__", UnboundCommand(fn, group=group))  # noqa: B010

        return fn

    return wrapper
