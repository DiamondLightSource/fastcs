import asyncio
from dataclasses import dataclass, field
from typing import Any

from softioc import softioc

from fastcs.controllers import ControllerAPI
from fastcs.logging import logger
from fastcs.transports.epics import (
    EpicsCAOptions,
    EpicsDocsOptions,
    EpicsGUIOptions,
)
from fastcs.transports.epics.ca.ioc import EpicsCAIOC
from fastcs.transports.epics.ca.util import validate_ca_id
from fastcs.transports.epics.docs import EpicsDocs
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.util import pv_prefix_from_path
from fastcs.transports.transport import Transport


@dataclass
class EpicsCATransport(Transport):
    """Channel access transport."""

    epicsca: EpicsCAOptions = field(default_factory=EpicsCAOptions)
    """CA-specific options. Currently empty; present as the YAML discriminator."""
    docs: EpicsDocsOptions | None = None
    """Options for the docs"""
    gui: EpicsGUIOptions | None = None
    """Options for the GUI. If not set, no GUI will be created."""

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        for api in controller_apis:
            validate_ca_id(api)
        self._controller_apis = controller_apis
        self._loop = loop
        self._pv_prefixes = [pv_prefix_from_path(api.path) for api in controller_apis]
        self._ioc = EpicsCAIOC(controller_apis)

        for api in controller_apis:
            if self.docs is not None:
                EpicsDocs(api).create_docs(self.docs)

            if self.gui is not None:
                EpicsGUI(api).create_gui(self.gui)

    async def serve(self) -> None:
        """Serve `ControllerAPI` over EPICS Channel Access"""
        logger.info("Running IOC", pv_prefixes=self._pv_prefixes)
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
        return f"EpicsCATransport({self._pv_prefixes})"
