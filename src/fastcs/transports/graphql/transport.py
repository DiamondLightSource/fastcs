import asyncio
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import ConfigDict

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport

from .graphql import GraphQLServer
from .options import GraphQLServerOptions
from .util import validate_graphql_id


@dataclass
class GraphQLTransport(Transport):
    """GraphQL transport."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    graphql: GraphQLServerOptions = field(default_factory=GraphQLServerOptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        for api in controller_apis:
            if api.path:
                validate_graphql_id(api.path[0])
        self._server = GraphQLServer(controller_apis)

    async def serve(self) -> None:
        await self._server.serve(self.graphql)

    def __repr__(self) -> str:
        return f"GraphQLTransport({self.graphql.host}:{self.graphql.port})"
