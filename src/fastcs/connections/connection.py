from abc import ABC, abstractmethod


class Connection(ABC):
    """A link to a device.

    Passed to a `ControllerSpec`. The runner opens it before the controller is
    constructed, and closes it when the application stops.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Open the link, or raise."""

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""
