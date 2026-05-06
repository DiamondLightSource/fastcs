import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport

from .dsr import TangoDSR, TangoDSROptions
from .util import validate_tango_id


@dataclass
class TangoTransport(Transport):
    """Tango transport."""

    tango: TangoDSROptions = field(default_factory=TangoDSROptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        for api in controller_apis:
            validate_tango_id(api.path[0])
        self._dsr = TangoDSR(controller_apis, loop)

    async def serve(self) -> None:
        coro = asyncio.to_thread(self._dsr.run, self.tango)
        await coro
