import asyncio
from dataclasses import dataclass, field
from typing import Any

from softioc import softioc

from fastcs.controllers import ControllerAPI
from fastcs.logging import logger
from fastcs.transports.epics import (
    EpicsDocsOptions,
    EpicsGUIOptions,
    EpicsIOCOptions,
)
from fastcs.transports.epics.ca.ioc import EpicsCAIOC
from fastcs.transports.epics.docs import EpicsDocs
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.transport import Transport


@dataclass
class EpicsCATransport(Transport):
    """Channel access transport."""

    epicsca: EpicsIOCOptions = field(default_factory=EpicsIOCOptions)
    """Options for the IOC"""
    docs: EpicsDocsOptions | None = None
    """Options for the docs"""
    gui: EpicsGUIOptions | None = None
    """Options for the GUI. If not set, no GUI will be created."""

    def connect(
        self,
        controller_api: ControllerAPI,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._controller_api = controller_api
        self._loop = loop
        self._pv_prefix = self.epicsca.pv_prefix
        self._ioc = EpicsCAIOC(self.epicsca.pv_prefix, controller_api)

        if self.docs is not None:
            EpicsDocs(self._controller_api).create_docs(self.docs)

        if self.gui is not None:
            EpicsGUI(self._controller_api, self._pv_prefix).create_gui(self.gui)

    async def serve(self) -> None:
        """Serve `ControllerAPI` over EPICS Channel Access"""
        logger.info("Running IOC", pv_prefix=self._pv_prefix)
        self._ioc.run(self._loop)

    @property
    def context(self) -> dict[str, Any]:
        """Provide common IOC commands such as dbl, dbgf, etc."""
        return {
            command_name: getattr(softioc, command_name)
            for command_name in softioc.command_names
            if command_name != "exit"
        }

    def __repr__(self):
        return f"EpicsCATransport({self._pv_prefix})"
