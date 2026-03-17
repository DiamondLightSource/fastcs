import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Union

from fastcs.controllers import ControllerAPI


@dataclass
class Transport:
    """A base class for transport's implementation
    so it can be used in FastCS."""

    subclasses: ClassVar[list[type["Transport"]]] = []

    def __init_subclass__(cls):
        cls.subclasses.append(cls)

    @classmethod
    def union(cls):
        if not cls.subclasses:
            raise RuntimeError("No Transports found")

        return Union[tuple(cls.subclasses)]  # noqa: UP007

    @abstractmethod
    def connect(
        self, controller_api: ControllerAPI, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Connect the ``Transport`` to the control system

        The `ControllerAPI` should be exposed over the transport. The provided event
        loop should be used where required instead of creating a new one.

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

        This method will be spawned as an async background task in before launching the
        interactive shell, so it can (but doesn't have to) block and run forever.

        """
        pass
