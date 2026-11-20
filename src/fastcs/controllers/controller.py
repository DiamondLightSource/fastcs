from collections.abc import Sequence
from typing import Any, Generic, cast

from typing_extensions import TypeVar

from fastcs.attributes import AnyAttributeIO
from fastcs.connections import Connection
from fastcs.controllers.base_controller import BaseController

# Connection[Any], as each connection class is generic in what it introspects
ConnectionT = TypeVar(
    "ConnectionT",
    bound=Connection[Any] | None,
    default=Connection[Any] | None,
    covariant=True,
)
"""The `Connection` a `Controller` holds, or ``None`` if it holds none"""


class Controller(BaseController, Generic[ConnectionT]):
    """Controller containing Attributes and named sub Controllers

    Args:
        connection: The connection this controller uses, if any

    """

    def __init__(
        self,
        description: str | None = None,
        ios: Sequence[AnyAttributeIO] | None = None,
        connection: ConnectionT | None = None,
    ) -> None:
        super().__init__(description=description, ios=ios)
        self.__connection = connection

    @property
    def connection(self) -> ConnectionT:
        """The connection this controller was given, or ``None``"""
        # Only None if no connection was given, which ConnectionT allows by default
        return cast(ConnectionT, self.__connection)

    def __setattr__(self, name, value):
        if name == "connection":
            raise AttributeError(
                f"Cannot assign {self}.connection. Pass the connection to "
                "super().__init__(connection=...) instead."
            )
        super().__setattr__(name, value)

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)
