from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar


class Connection(ABC):
    """A link to a device.

    Passed to a `ControllerSpec`. The runner opens it before the controller is
    constructed, and closes it when the application stops.
    """

    depends_on: ClassVar[type[Connection] | Sequence[type[Connection]]] = ()
    """The connection type, or types, this one needs before it can work.

    This connection depends on every connection of these types in the same
    `ControllerSpec`. The runner opens those first, and closes them last::

        class OdinConnection(Connection):
            depends_on = EigerConnection
    """

    @abstractmethod
    async def connect(self) -> None:
        """Open the link, or raise."""

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""
