import asyncio
import dataclasses
import functools
import inspect
import json
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    Optional,
    Union,
    get_origin,
    get_type_hints,
)

import typer
from pydantic import BaseModel, Field, ValidationError, create_model
from ruamel.yaml import YAML

from fastcs import __version__
from fastcs.connections import Connection
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerSpec
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


@dataclasses.dataclass(frozen=True)
class _RegisteredClass:
    cls: type[Controller]
    connection_types: dict[str, type[Connection]]
    options_name: str | None
    options_type: Any


_ENTRY_REGISTRY: dict[type[BaseModel], _RegisteredClass] = {}
"""Maps each Entry model to its Controller class, connections and options"""


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
            ``__init__`` taking any number of ``Connection`` arguments and at most
            one options argument.
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

    Defaults to ``<top-level-package>.<ClassName>`` so that Controllers from
    independently-distributed packages cannot collide. May be overridden
    verbatim (no prefix added) by setting ``type_name: ClassVar[str]`` on
    the Controller class.
    """
    top_level_package = controller_class.__module__.split(".", 1)[0]
    default = f"{top_level_package}.{controller_class.__name__}"
    return getattr(controller_class, "type_name", default)


def _launch(
    controller_classes: type[Controller] | list[type[Controller]],
    version: str | None = None,
) -> typer.Typer:
    classes = _normalise_classes(controller_classes)
    fastcs_options = _build_options_model(classes)
    app_name = classes[0].__name__ if len(classes) == 1 else "FastCS"
    launch_typer = typer.Typer()

    class LaunchContext:
        def __init__(self, fastcs_options):
            self.fastcs_options = fastcs_options

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
        ctx.obj = LaunchContext(fastcs_options)

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

        specs = _controller_specs(instance_options.controllers)

        instance = FastCS(
            specs,
            instance_options.transport,
            loop=asyncio.get_event_loop(),
        )

        instance.run()

    return launch_typer


def _controller_specs(
    controllers_options: list[Any],
) -> list[ControllerSpec]:
    """Create a `ControllerSpec` for each entry under `controllers:`.

    Raises:
        LaunchError: If two entries have the same id

    """
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    for entry in controllers_options:
        if entry.id in seen_ids:
            duplicates.append(entry.id)
        seen_ids.add(entry.id)
    if duplicates:
        raise LaunchError(
            f"Duplicate controller id(s) in `controllers:`: {sorted(set(duplicates))}"
        )

    specs: list[ControllerSpec] = []
    for entry in controllers_options:
        entry_cls: type[BaseModel] = type(entry)
        registered = _ENTRY_REGISTRY[entry_cls]

        kwargs: dict[str, Any] = {}
        for name, connection_type in registered.connection_types.items():
            settings = getattr(entry.connections, name)
            values = {k: getattr(settings, k) for k in type(settings).model_fields}
            kwargs[name] = connection_type(**values)

        if registered.options_name is not None:
            field_values = {
                name: getattr(entry, name)
                for name in entry_cls.model_fields
                if name not in ("id", "type", "connections")
            }
            kwargs[registered.options_name] = registered.options_type(**field_values)

        specs.append(ControllerSpec(entry.id, registered.cls, **kwargs))
    return specs


@functools.cache
def _connection_settings_model(connection_type: type[Connection]) -> type[BaseModel]:
    """Build the Pydantic model for one connection's block under `connections:`.

    Raises:
        LaunchError: If a constructor argument has no type hint

    """
    sig = inspect.signature(connection_type.__init__)
    hints = get_type_hints(connection_type.__init__)
    fields: dict[str, Any] = {}
    for name, param in list(sig.parameters.items())[1:]:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if name not in hints:
            raise LaunchError(
                f"Expected typehinting in '{connection_type.__name__}.__init__' "
                f"but received {sig}. Add a typehint for `{name}`."
            )
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (hints[name], default)

    return create_model(
        f"{connection_type.__name__}Settings", __config__={"extra": "forbid"}, **fields
    )


def _options_field_definitions(options_type: type) -> dict[str, tuple[Any, Any]]:
    """Field-by-field definitions for inlining ``options_type`` into an Entry.

    Returns a mapping suitable for splatting into ``create_model``. Supports
    pydantic ``BaseModel`` and (stdlib or pydantic) dataclasses; raises
    ``LaunchError`` for anything else.
    """
    if isinstance(options_type, type) and issubclass(options_type, BaseModel):
        return {
            name: (field.annotation, field)
            for name, field in options_type.model_fields.items()
        }
    if dataclasses.is_dataclass(options_type):
        hints = get_type_hints(options_type)
        result: dict[str, tuple[Any, Any]] = {}
        for f in dataclasses.fields(options_type):
            annotation = hints.get(f.name, f.type)
            if f.default is not dataclasses.MISSING:
                default: Any = f.default
            elif f.default_factory is not dataclasses.MISSING:
                default = Field(default_factory=f.default_factory)
            else:
                default = ...
            result[f.name] = (annotation, default)
        return result
    raise LaunchError(
        f"Cannot inline options type {options_type!r}: expected a dataclass "
        f"or pydantic BaseModel."
    )


def _build_entry_model(controller_class: type[Controller]) -> type[BaseModel]:
    """Build a Pydantic model for one entry under `controllers:`.

    Raises:
        LaunchError: If ``__init__`` is missing a type hint or takes more than one
            options argument, or an options field uses a reserved key

    """
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)[0][1:]
    hints = get_type_hints(controller_class.__init__)
    discriminator = _discriminator(controller_class)

    fields: dict[str, Any] = {
        "id": (str, ...),
        "type": (Literal[discriminator], ...),
    }

    connection_types: dict[str, type[Connection]] = {}
    for name in args:
        # A parametrised hint like IPConnection[Introspection] is checked by its class
        hint = get_origin(hints.get(name)) or hints.get(name)
        if isinstance(hint, type) and issubclass(hint, Connection):
            connection_types[name] = hint

    if connection_types:
        connection_fields: dict[str, Any] = {
            name: (_connection_settings_model(connection_type), ...)
            for name, connection_type in connection_types.items()
        }
        connections_model = create_model(
            f"{controller_class.__name__}Connections",
            __config__={"extra": "forbid"},
            **connection_fields,
        )
        fields["connections"] = (connections_model, ...)

    option_args = [name for name in args if name not in connection_types]
    options_name: str | None = None
    options_type: Any = None

    if len(option_args) == 1:
        options_name = option_args[0]
        if options_name not in hints:
            raise LaunchError(
                f"Expected typehinting in '{controller_class.__name__}"
                f".__init__' but received {sig}. Add a typehint for `{options_name}`."
            )
        options_type = hints[options_name]
        options_fields = _options_field_definitions(options_type)
        for reserved in ("id", "type", "connections"):
            if reserved in options_fields:
                raise LaunchError(
                    f"Options type {options_type.__name__} for "
                    f"{controller_class.__name__} declares a {reserved!r} field, "
                    f"which collides with a launch-framework key."
                )
        fields.update(options_fields)
    elif len(option_args) > 1:
        raise LaunchError(
            f"Expected no more than 1 argument besides connections for "
            f"'{controller_class.__name__}.__init__' but received "
            f"{len(option_args)} as `{sig}`"
        )

    entry_model = create_model(
        f"{controller_class.__name__}Entry",
        __config__={"extra": "forbid"},
        **fields,
    )
    _ENTRY_REGISTRY[entry_model] = _RegisteredClass(
        cls=controller_class,
        connection_types=connection_types,
        options_name=options_name,
        options_type=options_type,
    )
    return entry_model


def _build_options_model(
    controller_classes: list[type[Controller]],
) -> type[BaseModel]:
    """Build the top-level Pydantic model for fastcs.yaml.

    `controllers:` is a list of entries. Each entry is either the single
    registered class's entry model or a discriminated union over all
    registered classes; in both cases the entry's ``id:`` and ``type:``
    fields are required. Duplicate ``id`` values across the list are
    rejected by ``_controller_specs``.
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
        controllers=(list[entry_value_type], ...),
        transport=(list[Transport.union()], ...),
    )


def get_controller_schema(
    target: type[Controller] | list[type[Controller]],
) -> dict[str, Any]:
    """Gets schema for given controller class(es) for serialisation."""
    options_model = _build_options_model(_normalise_classes(target))
    return options_model.model_json_schema()
