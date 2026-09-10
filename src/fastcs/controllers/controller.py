import asyncio
from collections import defaultdict
from collections.abc import Sequence

from fastcs.attributes.attr_r import AttrR
from fastcs.connections import Connection
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

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        if name.isdigit():
            raise ValueError(
                f"Cannot add sub controller {name}. "
                "Numeric-only names are not allowed; use ControllerVector instead"
            )
        return super().add_sub_controller(name, sub_controller)

    @property
    def connected(self) -> bool:
        """Whether this controller can talk to its device.

        A read-through to ``self.connection.connected`` - the connection is the one
        object that knows, and controllers sharing a connection all report the same
        value, which is correct because they share one link. A controller with no
        connection has nothing to read through to and is always ``True``.
        """
        connection: Connection | None = self.connection
        return connection is None or connection.connected

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

        The returned coroutine is gated on this controller's connection: while that
        connection is down it waits for the connection to come back rather than
        polling a link that cannot answer. A controller with no connection is never
        gated.

        The gate is the only thing a scan does about connection health. Failure is
        detected in exactly one place - the connection's own IO, which knows a dead
        transport from a device complaint - so a raising scan is logged and retried
        rather than being read as a disconnection here.

        Args:
            period: The period to run the scans at
            scans: A list of `ScanCallback` to run periodically

        Returns:
            A wrapper `ScanCallback` that runs all of the callbacks at a given period
        """

        async def scan_coro() -> None:
            while True:
                connection: Connection | None = self.connection
                if connection is not None and not connection.connected:
                    await connection.wait_up()
                    continue

                try:
                    await asyncio.gather(
                        asyncio.sleep(period), *[scan() for scan in scans]
                    )
                except Exception:
                    logger.exception("Exception in scan task", period=period)
                    # Do not spin: a scan that raises immediately would otherwise
                    # retry as fast as the event loop allows.
                    await asyncio.sleep(period)

        return scan_coro
