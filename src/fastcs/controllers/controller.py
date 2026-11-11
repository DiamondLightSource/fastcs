import asyncio
from collections import defaultdict
from collections.abc import Sequence

from fastcs.attributes.attr_r import AttrR
from fastcs.controllers.base_controller import BaseController
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.util import ONCE


class Controller(BaseController):
    """Controller containing Attributes and named sub Controllers"""

    def __init__(
        self,
        description: str | None = None,
    ) -> None:
        super().__init__(description=description)
        self._connected = False

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)

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

    def create_api_and_tasks(
        self,
    ) -> tuple[ControllerAPI, list[ScanCallback], list[ScanCallback]]:
        """Create api for transports tasks for FastCS backend

        Creates a tuple of
            - The `ControllerAPI` for this controller
            - Initial coroutines to be run once on startup
            - Periodic coroutines to run as background tasks

        Returns:
            tuple[ControllerAPI, list[ScanCallback], list[ScanCallback]]

        """
        controller_api = self._build_api(self._path)

        scan_dict: dict[float, list[ScanCallback]] = defaultdict(list)
        initial_coros: list[ScanCallback] = []

        for api in controller_api.walk_api():
            for method in api.scan_methods.values():
                if method.period is ONCE:
                    initial_coros.append(method.fn)
                else:
                    scan_dict[method.period].append(method.fn)

            for attribute in api.attributes.values():
                if not (isinstance(attribute, AttrR) and attribute.has_getter()):
                    continue

                poll_period = attribute.poll_period

                async def poll_attribute(attribute: AttrR = attribute) -> None:
                    await attribute.poll()

                if poll_period is ONCE:
                    initial_coros.append(poll_attribute)
                elif poll_period is not None:
                    scan_dict[poll_period].append(poll_attribute)

        periodic_scan_coros: list[ScanCallback] = []
        for period, methods in scan_dict.items():
            periodic_scan_coros.append(self._create_periodic_scan_coro(period, methods))

        return controller_api, periodic_scan_coros, initial_coros

    def _create_periodic_scan_coro(
        self, period: float, scans: Sequence[ScanCallback]
    ) -> ScanCallback:
        """Create a coroutine to run scans at a given period

        This returns a coroutine that runs scans at a given period. If an exception is
        raised in a callback it is caught and the updates for the controller are
        paused, waiting for `_connected` to be set back to true via the `reconnect`
        method.

        Args:
            period: The period to run the scans at
            scans: A list of `ScanCallback` to run periodically

        Returns:
            A wrapper `ScanCallback` that runs all of the callbacks at a given period
        """

        async def scan_coro() -> None:
            while True:
                if not self._connected:
                    await asyncio.sleep(1)
                    continue

                try:
                    await asyncio.gather(
                        asyncio.sleep(period), *[scan() for scan in scans]
                    )
                except Exception:
                    logger.exception("Exception in scan task", period=period)
                    self._connected = False

                    await asyncio.sleep(1)  # Wait so this message appears last
                    logger.error("Pausing scan tasks and waiting for reconnect")

        return scan_coro
