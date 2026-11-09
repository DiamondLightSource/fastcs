from collections.abc import Callable
from typing import Generic, ParamSpec

from fastcs.connections import Connection
from fastcs.controllers.controller import Controller

P = ParamSpec("P")


class ControllerSpec(Generic[P]):
    """A deferred call that constructs a top-level controller.

    Example::

        # MyController(connection, count=4), with path ["DEMO"]
        ControllerSpec("DEMO", MyController, connection, count=4)

    Args:
        id: The root of the controller's path, or ``None`` for an empty path
        controller_class: The controller class, or any callable returning one
        args: Positional arguments for ``controller_class``
        kwargs: Keyword arguments for ``controller_class``

    """

    def __init__(
        self,
        id: str | None,
        controller_class: Callable[P, Controller],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        self.id = id
        self.controller_class = controller_class
        self.args = args
        self.kwargs = kwargs

    @property
    def connections(self) -> list[Connection]:
        """The `Connection` s among the arguments"""
        values = [*self.args, *self.kwargs.values()]
        return [value for value in values if isinstance(value, Connection)]

    def construct(self) -> Controller:
        """Construct the controller and set its path"""
        controller = self.controller_class(*self.args, **self.kwargs)
        if self.id is not None:
            controller.set_path([self.id])

        return controller
