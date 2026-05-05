import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport, _expect_single

from .dsr import TangoDSR, TangoDSROptions


@dataclass
class TangoTransport(Transport):
    """Tango transport."""

    tango: TangoDSROptions = field(default_factory=TangoDSROptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        controller_api = _expect_single(controller_apis, "TangoTransport")
        self._dsr = TangoDSR(controller_api, loop)

    async def serve(self) -> None:
        coro = asyncio.to_thread(self._dsr.run, self.tango)
        await coro
