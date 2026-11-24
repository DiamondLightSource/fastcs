from collections.abc import Iterator, Mapping, MutableMapping, Sequence

from fastcs.attributes import AnyAttributeIO
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller import Controller
from fastcs.util import Controller_T


class ControllerVector(MutableMapping[int, Controller_T], BaseController):
    """Controller containing Attributes and indexed sub Controllers

    The sub controllers registered with this Controller should be instances of the same
    Controller type, distinguished only by an integer index. The indexes do not need
    to be continiguous.
    """

    def __init__(
        self,
        children: Mapping[int, Controller_T],
        description: str | None = None,
        ios: Sequence[AnyAttributeIO] | None = None,
    ) -> None:
        super().__init__(description=description, ios=ios)
        self._children: dict[int, Controller_T] = {}
        for index, child in children.items():
            self[index] = child

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        raise NotImplementedError(
            "Cannot add named sub controller to ControllerVector. "
            "Use __setitem__ instead, for indexed sub controllers. "
            "E.g., vector[1] = Controller()"
        )

    def __getitem__(self, key: int) -> Controller_T:
        try:
            return self._children[key]
        except KeyError as exception:
            raise KeyError(
                f"ControllerVector does not have Controller with key {key}"
            ) from exception

    def __setitem__(self, key: int, value: Controller_T) -> None:
        if not isinstance(key, int):
            msg = f"Expected int, got {key}"
            raise TypeError(msg)
        if not isinstance(value, Controller):
            msg = f"Expected Controller, got {value}"
            raise TypeError(msg)
        self._children[key] = value
        super().add_sub_controller(str(key), value)

    def __delitem__(self, key: int) -> None:
        raise NotImplementedError("Cannot delete sub controller from ControllerVector.")

    def __iter__(self) -> Iterator[int]:
        yield from self._children

    def __len__(self) -> int:
        return len(self._children)
