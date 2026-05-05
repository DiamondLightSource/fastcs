import asyncio
import inspect
import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union, get_type_hints

import typer
from pydantic import BaseModel, Field, ValidationError, create_model
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
    controller_classes: type[Controller] | list[type[Controller]],
    version: str | None = None,
) -> None:
    """
    Serves as an entry point for starting FastCS applications.

    By utilizing type hints in each Controller's __init__ method, this
    function provides a command-line interface to describe and gather the
    required configuration before instantiating the application.

    Args:
        controller_classes: One or more FastCS Controller classes to make
            available for instantiation. Each must have a type-hinted
            __init__ method and no more than 2 arguments. The chosen class
            for each id is selected by a ``type`` discriminator in the
            config; when a single class is registered, ``type`` may be
            omitted.
        version (Optional[str]): The version of the FastCS application.

    Raises:
        LaunchError: If a class's __init__ is not as expected.

    Typical usage:
        if __name__ == "__main__":
            launch(MyController)            # single class
            launch([MyControllerA, MyControllerB])  # multi-class
    """
    _launch(controller_classes, version)()


def _normalise_classes(
    controller_classes: type[Controller] | list[type[Controller]],
) -> list[type[Controller]]:
    if isinstance(controller_classes, list):
        if not controller_classes:
            raise LaunchError("launch() requires at least one Controller class")
        return controller_classes
    return [controller_classes]


def _discriminator(controller_class: type[Controller]) -> str:
    """Type discriminator used in fastcs.yaml under each entry's ``type:`` key.

    Defaults to the class ``__name__`` and may be overridden by setting
    ``type_name: ClassVar[str]`` on the Controller class.
    """
    return getattr(controller_class, "type_name", controller_class.__name__)


def _launch(
    controller_classes: type[Controller] | list[type[Controller]],
    version: str | None = None,
) -> typer.Typer:
    classes = _normalise_classes(controller_classes)
    fastcs_options = _build_options_model(classes)
    type_map = {_discriminator(cls): cls for cls in classes}
    app_name = classes[0].__name__ if len(classes) == 1 else "FastCS"
    launch_typer = typer.Typer()

    class LaunchContext:
        def __init__(self, classes, fastcs_options, type_map):
            self.classes = classes
            self.fastcs_options = fastcs_options
            self.type_map = type_map

    def version_callback(value: bool):
        if value:
            if version:
                print(f"{app_name}: {version}")
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
            help=f"Display the {app_name} version.",
        ),
    ):
        ctx.obj = LaunchContext(classes, fastcs_options, type_map)

    @launch_typer.command(help=f"Produce json schema for a {app_name}")
    def schema(ctx: typer.Context):
        system_schema = ctx.obj.fastcs_options.model_json_schema()
        print(json.dumps(system_schema, indent=2))

    @launch_typer.command(help=f"Start up a {app_name}")
    def run(
        ctx: typer.Context,
        config: Annotated[
            Path,
            typer.Argument(help=f"A yaml file matching the {app_name} schema"),
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
        """Start the controllers"""
        configure_logging(
            log_level, graylog_endpoint, graylog_static_fields, graylog_env_fields
        )

        fastcs_options = ctx.obj.fastcs_options
        type_map = ctx.obj.type_map

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

        controllers = _instantiate_controllers(instance_options.controllers, type_map)

        if len(controllers) > 1:
            raise LaunchError(
                "Multi-controller execution is not yet wired through FastCS; "
                "this lands in the next slice of issue #353. "
                "Configure exactly one entry under `controllers:` for now."
            )

        instance = FastCS(
            controllers[0],
            instance_options.transport,
            loop=asyncio.get_event_loop(),
        )

        instance.run()

    return launch_typer


def _instantiate_controllers(
    controllers_options: dict[str, Any],
    type_map: dict[str, type[Controller]],
) -> list[Controller]:
    """Instantiate each entry under `controllers:` and stamp its id.

    Each value in ``controllers_options`` is a dynamically-built Pydantic
    model whose fields are unknown to the type checker; the discriminator
    and optional controller options block are accessed by name at runtime.
    """
    controllers: list[Controller] = []
    for id, entry in controllers_options.items():
        cls = type_map[entry.type]
        if hasattr(entry, "controller"):
            controller = cls(entry.controller)
        else:
            controller = cls()
        controller.set_id(id)
        controllers.append(controller)
    return controllers


def _build_entry_model(controller_class: type[Controller]) -> type[BaseModel]:
    """Build a Pydantic model for one entry under `controllers:`.

    Each entry has a ``type`` discriminator literal and, for Controllers
    whose ``__init__`` accepts a typed options argument, a ``controller``
    options block.
    """
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)[0]
    discriminator = _discriminator(controller_class)

    fields: dict[str, Any] = {"type": (Literal[discriminator], discriminator)}

    if len(args) == 1:
        pass
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
        fields["controller"] = (options_type, ...)
    else:
        raise LaunchError(
            f"Expected no more than 2 arguments for '{controller_class.__name__}"
            f".__init__' but received {len(args)} as `{sig}`"
        )

    return create_model(
        f"{controller_class.__name__}Entry",
        __config__={"extra": "forbid"},
        **fields,
    )


def _build_options_model(
    controller_classes: list[type[Controller]],
) -> type[BaseModel]:
    """Build the top-level Pydantic model for fastcs.yaml.

    `controllers:` is a dict keyed by id. Each value is either the single
    registered class's entry model (in which case ``type`` is optional via
    its default) or a discriminated union over all registered classes
    (selected by the entry's ``type:`` field).
    """
    entries = [_build_entry_model(cls) for cls in controller_classes]

    if len(entries) == 1:
        entry_value_type: Any = entries[0]
        title = controller_classes[0].__name__
    else:
        entry_value_type = Annotated[
            Union[tuple(entries)], Field(discriminator="type")  # noqa: UP007
        ]
        title = "FastCS"

    return create_model(
        title,
        __config__={"extra": "forbid"},
        controllers=(dict[str, entry_value_type], ...),
        transport=(list[Transport.union()], ...),
    )


def get_controller_schema(
    target: type[Controller] | list[type[Controller]],
) -> dict[str, Any]:
    """Gets schema for given controller class(es) for serialisation."""
    options_model = _build_options_model(_normalise_classes(target))
    return options_model.model_json_schema()
