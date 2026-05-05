from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import strawberry
import uvicorn
from strawberry.asgi import GraphQL
from strawberry.tools import create_type
from strawberry.types.field import StrawberryField

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes.datatype import DType_T
from fastcs.exceptions import FastCSError
from fastcs.logging import intercept_std_logger

from .options import GraphQLServerOptions


class GraphQLServer:
    """A GraphQL server which serves one combined schema for N controllers.

    Each top-level controller is exposed as a Query (and, where applicable,
    Mutation) field keyed by the controller's id, so a single endpoint serves
    every configured device.
    """

    def __init__(self, controller_apis: list[ControllerAPI]):
        self._controller_apis = controller_apis
        self._app = self._create_app()

    def _create_app(self) -> GraphQL:
        queries: list[StrawberryField] = []
        mutations: list[StrawberryField] = []
        for controller_api in self._controller_apis:
            id = controller_api.path[0]
            sub_tree = GraphQLAPI(controller_api)
            if sub_tree.queries:
                queries.append(
                    _wrap_as_field(id, create_type(f"{id}Query", sub_tree.queries))
                )
            if sub_tree.mutations:
                mutations.append(
                    _wrap_as_field(id, create_type(f"{id}Mutation", sub_tree.mutations))
                )

        if not queries:
            raise FastCSError(
                "Can't create GraphQL transport from ControllerAPIs with no read "
                "attributes"
            )

        query = create_type("Query", queries)
        mutation = create_type("Mutation", mutations) if mutations else None
        schema = strawberry.Schema(query=query, mutation=mutation)
        return GraphQL(schema)

    async def serve(self, options: GraphQLServerOptions | None = None) -> None:
        options = options or GraphQLServerOptions()
        self._server = uvicorn.Server(
            uvicorn.Config(
                app=self._app,
                host=options.host,
                port=options.port,
                log_level=options.log_level,
            )
        )
        intercept_std_logger("uvicorn.error")
        intercept_std_logger("uvicorn.access")
        intercept_std_logger("uvicorn.asgi")

        await self._server.serve()


class GraphQLAPI:
    """A Strawberry sub-tree built dynamically from a single `ControllerAPI`.

    Produces the per-controller queries and mutations; the combined top-level
    schema is assembled by `GraphQLServer`.
    """

    def __init__(self, controller_api: ControllerAPI):
        self.queries: list[StrawberryField] = []
        self.mutations: list[StrawberryField] = []

        self._process_attributes(controller_api)
        self._process_commands(controller_api)
        self._process_sub_apis(controller_api)

    def _process_attributes(self, api: ControllerAPI):
        """Create queries and mutations from api attributes."""
        for attr_name, attribute in api.attributes.items():
            match attribute:
                # mutation for server changes https://graphql.org/learn/queries/
                case AttrRW():
                    self.queries.append(
                        strawberry.field(_wrap_attr_get(attr_name, attribute))
                    )
                    self.mutations.append(
                        strawberry.mutation(_wrap_attr_set(attr_name, attribute))
                    )
                case AttrR():
                    self.queries.append(
                        strawberry.field(_wrap_attr_get(attr_name, attribute))
                    )
                case AttrW():
                    self.mutations.append(
                        strawberry.mutation(_wrap_attr_set(attr_name, attribute))
                    )

    def _process_commands(self, controller_api: ControllerAPI):
        """Create mutations from api commands"""
        for name, method in controller_api.command_methods.items():
            self.mutations.append(strawberry.mutation(_wrap_command(name, method.fn)))

    def _process_sub_apis(self, root_controller_api: ControllerAPI):
        """Recursively add fields from the queries and mutations of sub apis"""
        for controller_api in root_controller_api.sub_apis.values():
            field_name = controller_api.path[-1]
            # Type name is path-joined so subs sharing a local name across two
            # top-level controllers produce distinct GraphQL types.
            type_stem = "_".join(controller_api.path)
            child_tree = GraphQLAPI(controller_api)
            if child_tree.queries:
                self.queries.append(
                    _wrap_as_field(
                        field_name,
                        create_type(f"{type_stem}_Query", child_tree.queries),
                    )
                )
            if child_tree.mutations:
                self.mutations.append(
                    _wrap_as_field(
                        field_name,
                        create_type(f"{type_stem}_Mutation", child_tree.mutations),
                    )
                )


def _wrap_attr_set(
    attr_name: str, attribute: AttrW[DType_T]
) -> Callable[[DType_T], Coroutine[Any, Any, None]]:
    """Wrap an attribute in a function with annotations for strawberry"""

    async def _dynamic_f(value):
        await attribute.put(value)
        return value

    # Add type annotations for validation, schema, conversions
    _dynamic_f.__name__ = attr_name
    _dynamic_f.__annotations__["value"] = attribute.datatype.dtype
    _dynamic_f.__annotations__["return"] = attribute.datatype.dtype

    return _dynamic_f


def _wrap_attr_get(
    attr_name: str, attribute: AttrR[DType_T]
) -> Callable[[], Coroutine[Any, Any, DType_T]]:
    """Wrap an attribute in a function with annotations for strawberry"""

    async def _dynamic_f() -> DType_T:
        return attribute.get()

    _dynamic_f.__name__ = attr_name
    _dynamic_f.__annotations__["return"] = attribute.datatype.dtype

    return _dynamic_f


def _wrap_as_field(field_name: str, operation: type) -> StrawberryField:
    """Wrap a strawberry type as a field of a parent type"""

    def _dynamic_field():
        return operation()

    _dynamic_field.__name__ = field_name
    _dynamic_field.__annotations__["return"] = operation

    return strawberry.field(_dynamic_field)


def _wrap_command(method_name: str, method: Callable) -> Callable[..., Awaitable[bool]]:
    """Wrap a command in a function with annotations for strawberry"""

    async def _dynamic_f() -> bool:
        await method()
        return True

    _dynamic_f.__name__ = method_name

    return _dynamic_f
