from collections.abc import Sequence

from fastcs.attributes import AnyAttributeIO
from fastcs.controllers.base_controller import BaseController


class Controller(BaseController):
    """Controller containing Attributes and named sub Controllers"""

    def __init__(
        self,
        description: str | None = None,
        ios: Sequence[AnyAttributeIO] | None = None,
    ) -> None:
        super().__init__(description=description, ios=ios)
        self._connected = False

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)

    @property
    def connected(self) -> bool:
        """Whether the controller is connected and its scan tasks should run"""
        return self._connected

    def mark_disconnected(self) -> None:
        """Mark the controller disconnected, pausing its scan tasks until reconnect"""
        self._connected = False

    async def connect(self) -> None:
        """Hook to perform initial connection to device

        This should set ``_connected`` to ``True`` if the connection was successful to
        enable scan tasks.

        """
        self._connected = True

    async def reconnect(self):
        """Hook to reconnect to device after an error

        This should set ``_connected`` to ``True`` if the connection was successful to
        enable scan tasks.

        If the connection cannot be re-established it should log an error with the
        reason. It should not raise an exception.

        """
        self._connected = True

    async def disconnect(self) -> None:
        """Hook to tidy up resources before stopping the application"""
        pass
