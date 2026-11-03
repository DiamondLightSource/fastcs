import asyncio
import dataclasses
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


@dataclasses.dataclass(frozen=True)
class _RegisteredClass:
    cls: type[Controller]
    expects_options: bool
    options_type: Any


_ENTRY_REGISTRY: dict[type[BaseModel], _RegisteredClass] = {}
"""Maps each dynamically-built Entry model class to its originating
Controller class and whether it expects an options arg (with the
options-type, if any). Populated by ``_build_entry_model`` and read by
``_instantiate_controllers``."""


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
            for each id is selected by a required ``type`` discriminator
            in the config.
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

        controllers = _instantiate_controllers(instance_options.controllers)

        instance = FastCS(
            controllers,
            instance_options.transport,
            loop=asyncio.get_event_loop(),
        )

        instance.run()

    return launch_typer


def _instantiate_controllers(
    controllers_options: list[Any],
) -> list[Controller]:
    """Instantiate each entry under `controllers:` with its path seeded.

    Each item in ``controllers_options`` is a dynamically-built Pydantic
    model that exposes ``id``, ``type`` and the controller's options fields
    inlined as siblings. The originating Controller class and its
    options-type are looked up in ``_ENTRY_REGISTRY`` (populated by
    ``_build_entry_model``). The entry's ``id`` is passed as ``path=[id]``
    to the controller constructor so that ``ControllerAPI.path`` is rooted
    at the YAML id.
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

    controllers: list[Controller] = []
    for entry in controllers_options:
        entry_cls: type[BaseModel] = type(entry)
        registered = _ENTRY_REGISTRY[entry_cls]
        if registered.expects_options:
            field_values = {
                name: getattr(entry, name)
                for name in entry_cls.model_fields
                if name not in ("id", "type")
            }
            controller = registered.cls(
                registered.options_type(**field_values), path=[entry.id]
            )
        else:
            controller = registered.cls(path=[entry.id])
        controllers.append(controller)
    return controllers


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

    Each entry exposes ``id`` and a ``type`` discriminator literal alongside
    the options-type's fields, inlined as siblings (no nested ``controller:``
    block). The Controller class and its options-type are recorded in
    ``_ENTRY_REGISTRY`` for use by ``_instantiate_controllers``.
    """
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)[0]
    discriminator = _discriminator(controller_class)

    fields: dict[str, Any] = {
        "id": (str, ...),
        "type": (Literal[discriminator], ...),
    }
    expects_options = False
    options_type: Any = None

    if len(args) == 1:
        pass
    elif len(args) == 2:
        expects_options = True
        hints = get_type_hints(controller_class.__init__)
        hints.pop("return", None)
        if not hints:
            raise LaunchError(
                f"Expected typehinting in '{controller_class.__name__}"
                f".__init__' but received {sig}. Add a typehint for `{args[-1]}`."
            )
        options_type = list(hints.values())[-1]
        options_fields = _options_field_definitions(options_type)
        for reserved in ("id", "type"):
            if reserved in options_fields:
                raise LaunchError(
                    f"Options type {options_type.__name__} for "
                    f"{controller_class.__name__} declares a {reserved!r} field, "
                    f"which collides with a launch-framework key."
                )
        fields.update(options_fields)
    else:
        raise LaunchError(
            f"Expected no more than 2 arguments for '{controller_class.__name__}"
            f".__init__' but received {len(args)} as `{sig}`"
        )

    entry_model = create_model(
        f"{controller_class.__name__}Entry",
        __config__={"extra": "forbid"},
        **fields,
    )
    _ENTRY_REGISTRY[entry_model] = _RegisteredClass(
        cls=controller_class,
        expects_options=expects_options,
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
    rejected by ``_instantiate_controllers``.
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
