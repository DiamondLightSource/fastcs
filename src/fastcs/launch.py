import inspect
import json
from pathlib import Path
from typing import Annotated, Any, Optional, get_type_hints

import typer
from pydantic import BaseModel, ValidationError, create_model
from ruamel.yaml import YAML

from fastcs import __version__
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.exceptions import LaunchError
from fastcs.logging import (
    GraylogEndpoint,
    GraylogEnvFields,
    GraylogStaticFields,
    LogLevel,
    configure_logging,
    parse_graylog_env_fields,
    parse_graylog_static_fields,
)
from fastcs.transports import Transport


def launch(
    controller_class: type[Controller],
    version: str | None = None,
) -> None:
    """
    Serves as an entry point for starting FastCS applications.

    By utilizing type hints in a Controller's __init__ method, this
    function provides a command-line interface to describe and gather the
    required configuration before instantiating the application.

    Args:
        controller_class (type[Controller]): The FastCS Controller to instantiate.
            It must have a type-hinted __init__ method and no more than 2 arguments.
        version (Optional[str]): The version of the FastCS Controller.
            Optional

    Raises:
        LaunchError: If the class's __init__ is not as expected

    Example of the expected Controller implementation:
        class MyController(Controller):
            def __init__(self, my_arg: MyControllerOptions) -> None:
                ...

    Typical usage:
        if __name__ == "__main__":
            launch(MyController)
    """
    _launch(controller_class, version)()


def _launch(
    controller_class: type[Controller],
    version: str | None = None,
) -> typer.Typer:
    fastcs_options = _extract_options_model(controller_class)
    launch_typer = typer.Typer()

    class LaunchContext:
        def __init__(self, controller_class, fastcs_options):
            self.controller_class = controller_class
            self.fastcs_options = fastcs_options

    def version_callback(value: bool):
        if value:
            if version:
                print(f"{controller_class.__name__}: {version}")
            print(f"FastCS: {__version__}")
            raise typer.Exit()

    @launch_typer.callback()
    def main(
        ctx: typer.Context,
        version: Optional[bool] = typer.Option(  # noqa (Optional required for typer)
            None,
            "--version",
            callback=version_callback,
            is_eager=True,
            help=f"Display the {controller_class.__name__} version.",
        ),
    ):
        ctx.obj = LaunchContext(
            controller_class,
            fastcs_options,
        )

    @launch_typer.command(help=f"Produce json schema for a {controller_class.__name__}")
    def schema(ctx: typer.Context):
        system_schema = ctx.obj.fastcs_options.model_json_schema()
        print(json.dumps(system_schema, indent=2))

    @launch_typer.command(help=f"Start up a {controller_class.__name__}")
    def run(
        ctx: typer.Context,
        config: Annotated[
            Path,
            typer.Argument(
                help=f"A yaml file matching the {controller_class.__name__} schema"
            ),
        ],
        log_level: Annotated[LogLevel, typer.Option()] = LogLevel.INFO,
        graylog_endpoint: Annotated[
            Optional[GraylogEndpoint],  # noqa: UP045
            typer.Option(
                help="Endpoint for graylog logging - '<host>:<port>'",
                parser=GraylogEndpoint.parse_graylog_endpoint,
            ),
        ] = None,
        graylog_static_fields: Annotated[
            Optional[GraylogStaticFields],  # noqa: UP045
            typer.Option(
                help="Fields to add to graylog messages with static values",
                parser=parse_graylog_static_fields,
            ),
        ] = None,
        graylog_env_fields: Annotated[
            Optional[GraylogEnvFields],  # noqa: UP045
            typer.Option(
                help="Fields to add to graylog messages from environment variables",
                parser=parse_graylog_env_fields,
            ),
        ] = None,
    ):
        """
        Start the controller
        """
        configure_logging(
            log_level, graylog_endpoint, graylog_static_fields, graylog_env_fields
        )

        controller_class = ctx.obj.controller_class
        fastcs_options = ctx.obj.fastcs_options

        yaml = YAML(typ="safe")
        options_yaml = yaml.load(config)

        try:
            instance_options = fastcs_options.model_validate(options_yaml)
        except ValidationError as e:
            if any("transport" in error["loc"] for error in json.loads(e.json())):
                raise LaunchError(
                    "Failed to validate transports. "
                    "Are the correct fastcs extras installed? "
                    f"Available transports:\n{Transport.subclasses}",
                ) from e

            raise LaunchError("Failed to validate config") from e

        if hasattr(instance_options, "controller"):
            controller = controller_class(instance_options.controller)
        else:
            controller = controller_class()

        instance = FastCS(controller, instance_options.transport)

        instance.run()

    return launch_typer


def _extract_options_model(controller_class: type[Controller]) -> type[BaseModel]:
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)[0]
    if len(args) == 1:
        fastcs_options = create_model(
            f"{controller_class.__name__}",
            transport=(list[Transport.union()], ...),
            __config__={"extra": "forbid"},
        )
    elif len(args) == 2:
        hints = get_type_hints(controller_class.__init__)
        if "return" in hints:
            del hints["return"]
        if hints:
            options_type = list(hints.values())[-1]
        else:
            raise LaunchError(
                f"Expected typehinting in '{controller_class.__name__}"
                f".__init__' but received {sig}. Add a typehint for `{args[-1]}`."
            )
        fastcs_options = create_model(
            f"{controller_class.__name__}",
            controller=(options_type, ...),
            transport=(list[Transport.union()], ...),
            __config__={"extra": "forbid"},
        )
    else:
        raise LaunchError(
            f"Expected no more than 2 arguments for '{controller_class.__name__}"
            f".__init__' but received {len(args)} as `{sig}`"
        )
    return fastcs_options


def get_controller_schema(target: type[Controller]) -> dict[str, Any]:
    """Gets schema for a give controller for serialisation."""
    options_model = _extract_options_model(target)
    target_schema = options_model.model_json_schema()
    return target_schema
