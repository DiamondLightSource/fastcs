from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller_api import ControllerAPI


class Controller(BaseController):
    """Controller containing Attributes and named sub Controllers"""

    def __init__(
        self,
        description: str | None = None,
    ) -> None:
        super().__init__(description=description)

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)

    def create_api(self) -> ControllerAPI:
        """Create the `ControllerAPI` of this controller and its sub controllers."""
        return self._build_api(self._path)
