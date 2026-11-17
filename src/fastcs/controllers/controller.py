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

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)
