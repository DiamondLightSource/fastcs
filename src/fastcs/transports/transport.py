from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Union

from fastcs.controllers import ControllerAPI


@dataclass
class Transport:
    """A base class for transport's implementation so it can be used in FastCS."""

    subclasses: ClassVar[list[type["Transport"]]] = []

    def __init_subclass__(cls):
        cls.subclasses.append(cls)

    @classmethod
    def union(cls):
        if not cls.subclasses:
            raise RuntimeError("No Transports found")

        return Union[tuple(cls.subclasses)]  # noqa: UP007

    @abstractmethod
    def connect(self, controller_api: ControllerAPI) -> None:
        """Connect the `Transport` to the control system

        The `ControllerAPI` should be exposed over the transport. Transports that
        require the event loop should retrieve it in `serve` with
        `asyncio.get_running_loop`, as this method is called from within an async
        context.

        """
        pass

    @property
    def context(self) -> dict[str, Any]:
        """Functions and variables to add to the context of the interactive shell

        See `FastCS.serve` for usage.

        """
        return {}

    @abstractmethod
    async def serve(self) -> None:
        """Serve the `ControllerAPI`

        This method will be called in an async context as a background task before
        launching the interactive shell, so it can (but doesn't have to) block and run
        forever. To retrieve the fastcs event loop use `asyncio.get_running_loop`.

        """
        pass
