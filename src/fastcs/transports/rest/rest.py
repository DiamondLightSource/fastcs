from collections.abc import Callable, Coroutine
from typing import Any

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, create_model

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes.datatype import DType_T
from fastcs.logging import intercept_std_logger
from fastcs.methods import Command
from fastcs.util import snake_to_pascal

from .options import RestServerOptions
from .util import (
    cast_from_rest_type,
    cast_to_rest_type,
    convert_datatype,
)


class RestServer:
    """A Rest Server which handles one or more controllers."""

    def __init__(self, controller_apis: list[ControllerAPI]):
        self._controller_apis = controller_apis
        self._app = self._create_app()

    def _create_app(self):
        app = FastAPI()
        for controller_api in self._controller_apis:
            _add_attribute_api_routes(app, controller_api)
            _add_command_api_routes(app, controller_api)

        return app

    async def serve(self, options: RestServerOptions | None):
        options = options or RestServerOptions()
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


def _put_request_body(attribute: AttrW[DType_T]):
    """
    Creates a pydantic model for each datatype which defines the schema
    of the PUT request body
    """
    converted_datatype = convert_datatype(attribute.datatype)
    type_name = str(attribute.datatype.dtype.__name__).title()
    # key=(type, ...) to declare a field without default value
    return create_model(
        f"Put{type_name}Value",
        value=(converted_datatype, ...),
    )


def _wrap_attr_put(
    attribute: AttrW[DType_T],
) -> Callable[[DType_T], Coroutine[Any, Any, None]]:
    async def attr_put(request):
        await attribute.set(cast_from_rest_type(attribute.datatype, request.value))

    # Fast api uses type annotations for validation, schema, conversions
    attr_put.__annotations__["request"] = _put_request_body(attribute)

    return attr_put


def _get_response_body(attribute: AttrR[DType_T]):
    """
    Creates a pydantic model for each datatype which defines the schema
    of the GET request body
    """
    converted_datatype = convert_datatype(attribute.datatype)
    type_name = str(converted_datatype.__name__).title()
    # key=(type, ...) to declare a field without default value
    return create_model(
        f"Get{type_name}Value",
        value=(converted_datatype, ...),
    )


def _wrap_attr_get(
    attribute: AttrR[DType_T],
) -> Callable[[], Coroutine[Any, Any, dict[str, object]]]:
    async def attr_get() -> dict[str, object]:
        value = attribute.readback
        return {"value": cast_to_rest_type(attribute.datatype, value)}

    return attr_get


def _add_attribute_api_routes(app: FastAPI, root_controller_api: ControllerAPI) -> None:
    for controller_api in root_controller_api.walk_api():
        path = controller_api.path

        for attr_name, attribute in controller_api.attributes.items():
            attr_name = attr_name.replace("_", "-")
            route = f"{'/'.join(path)}/{attr_name}" if path else attr_name

            match attribute:
                # https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods
                case AttrRW():
                    app.add_api_route(
                        f"/{route}",
                        _wrap_attr_get(attribute),
                        methods=["GET"],  # Idempotent and safe data retrieval,
                        status_code=200,  # https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods/GET
                        response_model=_get_response_body(attribute),
                    )
                    app.add_api_route(
                        f"/{route}",
                        _wrap_attr_put(attribute),
                        methods=["PUT"],  # Idempotent state change
                        status_code=204,  # https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods/PUT
                    )
                case AttrR():
                    app.add_api_route(
                        f"/{route}",
                        _wrap_attr_get(attribute),
                        methods=["GET"],
                        status_code=200,
                        response_model=_get_response_body(attribute),
                    )
                case AttrW():
                    app.add_api_route(
                        f"/{route}",
                        _wrap_attr_put(attribute),
                        methods=["PUT"],
                        status_code=204,
                    )


def _command_arguments_body(name: str, command: Command) -> type[BaseModel]:
    """A pydantic model of a command's positional arguments, as a request body."""
    parameters = list(command.signature.parameters.values())
    # key=(type, ...) to declare a field without default value
    fields: dict[str, Any] = {
        parameter.name: (argument_type, ...)
        for parameter, argument_type in zip(
            parameters, command.argument_types, strict=True
        )
    }
    return create_model(f"Call{snake_to_pascal(name)}Arguments", **fields)


def _command_response_body(name: str, return_datatype: type) -> type[BaseModel]:
    fields: dict[str, Any] = {"value": (return_datatype, ...)}
    return create_model(f"Call{snake_to_pascal(name)}Result", **fields)


def _wrap_command(
    name: str, command: Command
) -> Callable[..., Coroutine[None, None, dict[str, object] | None]]:
    """Wrap a command in a route handler that carries its arguments and result."""
    argument_names = [
        parameter.name for parameter in command.signature.parameters.values()
    ]
    returns_a_value = command.return_datatype is not None

    if not argument_names:

        async def call() -> dict[str, object] | None:
            result = await command.fn()
            return {"value": result} if returns_a_value else None

        return call

    async def call_with_arguments(request) -> dict[str, object] | None:
        arguments = [getattr(request, argument) for argument in argument_names]
        result = await command.fn(*arguments)
        return {"value": result} if returns_a_value else None

    # Fast api uses type annotations for validation, schema, conversions
    call_with_arguments.__annotations__["request"] = _command_arguments_body(
        name, command
    )

    return call_with_arguments


def _add_command_api_routes(app: FastAPI, root_controller_api: ControllerAPI) -> None:
    for controller_api in root_controller_api.walk_api():
        path = controller_api.path

        for name, method in controller_api.command_methods.items():
            cmd_name = name.replace("_", "-")
            route = f"{'/'.join(path)}/{cmd_name}" if path else cmd_name
            return_datatype = method.return_datatype
            app.add_api_route(
                f"/{route}",
                _wrap_command(name, method),
                methods=["PUT"],
                # A command that gives something back has a body to return, so
                # it answers 200 rather than 204 No Content.
                status_code=200 if return_datatype is not None else 204,
                response_model=(
                    _command_response_body(name, return_datatype)
                    if return_datatype is not None
                    else None
                ),
            )
