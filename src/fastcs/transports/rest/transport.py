import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport

from .options import RestServerOptions
from .rest import RestServer
from .util import validate_rest_id


@dataclass
class RestTransport(Transport):
    """Rest Transport Adapter."""

    rest: RestServerOptions = field(default_factory=RestServerOptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        for api in controller_apis:
            if api.path:
                validate_rest_id(api.path[0])
        self._server = RestServer(controller_apis)

    async def serve(self) -> None:
        await self._server.serve(self.rest)

    def __repr__(self) -> str:
        return f"RestTransport({self.rest.host}:{self.rest.port})"
