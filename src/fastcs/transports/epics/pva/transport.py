import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.logging import logger
from fastcs.transports.epics import (
    EpicsDocsOptions,
    EpicsGUIOptions,
    EpicsPVAOptions,
)
from fastcs.transports.epics.emission import emit_docs_files, emit_gui_files
from fastcs.transports.epics.pva.gui import PvaEpicsGUI
from fastcs.transports.epics.pva.util import validate_pva_id
from fastcs.transports.epics.util import pv_prefix_from_path
from fastcs.transports.transport import Transport

from .ioc import P4PIOC


@dataclass
class EpicsPVATransport(Transport):
    """PV access transport."""

    epicspva: EpicsPVAOptions = field(default_factory=EpicsPVAOptions)
    """PVA-specific options. Currently empty; present as the YAML discriminator."""
    docs: EpicsDocsOptions | None = None
    gui: EpicsGUIOptions | None = None

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        for api in controller_apis:
            validate_pva_id(api)
        self._controller_apis = controller_apis
        self._pv_prefixes = [pv_prefix_from_path(api.path) for api in controller_apis]
        self._ioc = P4PIOC(controller_apis)

        if self.docs is not None:
            emit_docs_files(controller_apis, self.docs)

        if self.gui is not None:
            emit_gui_files(controller_apis, self.gui, PvaEpicsGUI)

    async def serve(self) -> None:
        """Serve `ControllerAPI` over EPICS PVAccess"""
        logger.info("Running IOC", pv_prefixes=self._pv_prefixes)
        await self._ioc.run()

    def __repr__(self):
        return f"EpicsPVATransport({self._pv_prefixes})"
