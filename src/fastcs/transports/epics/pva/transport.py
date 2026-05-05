import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.logging import logger
from fastcs.transports.epics import (
    EpicsDocsOptions,
    EpicsGUIOptions,
    EpicsPVAOptions,
)
from fastcs.transports.epics.docs import EpicsDocs
from fastcs.transports.epics.pva.gui import PvaEpicsGUI
from fastcs.transports.epics.util import pv_prefix_from_path
from fastcs.transports.transport import Transport, _expect_single

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
        controller_api = _expect_single(controller_apis, "EpicsPVATransport")
        self._controller_api = controller_api
        self._pv_prefix = pv_prefix_from_path(controller_api.path)
        self._ioc = P4PIOC(controller_api)

        if self.docs is not None:
            EpicsDocs(self._controller_api).create_docs(self.docs)

        if self.gui is not None:
            PvaEpicsGUI(self._controller_api).create_gui(self.gui)

    async def serve(self) -> None:
        """Serve `ControllerAPI` over EPICS PVAccess"""
        logger.info("Running IOC", pv_prefix=self._pv_prefix)
        await self._ioc.run()

    def __repr__(self):
        return f"EpicsPVATransport({self._pv_prefix})"
