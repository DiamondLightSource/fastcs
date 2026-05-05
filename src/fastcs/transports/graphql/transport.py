import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport, _expect_single

from .graphql import GraphQLServer
from .options import GraphQLServerOptions


@dataclass
class GraphQLTransport(Transport):
    """GraphQL transport."""

    graphql: GraphQLServerOptions = field(default_factory=GraphQLServerOptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        controller_api = _expect_single(controller_apis, "GraphQLTransport")
        self._server = GraphQLServer(controller_api)

    async def serve(self) -> None:
        await self._server.serve(self.graphql)

    def __repr__(self) -> str:
        return f"GraphQLTransport({self.graphql.host}:{self.graphql.port})"
