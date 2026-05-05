import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport, _expect_single

from .options import RestServerOptions
from .rest import RestServer


@dataclass
class RestTransport(Transport):
    """Rest Transport Adapter."""

    rest: RestServerOptions = field(default_factory=RestServerOptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        controller_api = _expect_single(controller_apis, "RestTransport")
        self._server = RestServer(controller_api)

    async def serve(self) -> None:
        await self._server.serve(self.rest)

    def __repr__(self) -> str:
        return f"RestTransport({self.rest.host}:{self.rest.port})"
