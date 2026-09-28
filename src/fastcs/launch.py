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
from fastcs.connections import (
    DEFAULT_RECONNECT_ATTEMPTS,
    DEFAULT_RECONNECT_PERIOD,
    Connection,
    Supervisor,
)
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

CONNECTIONS_KEY = "connections"
"""The key under an entry that configures its controller's connections.

One sub-key per connection-typed argument of the controller's ``__init__``, so the
schema knows exactly which names are required and what each one's settings are.
"""

_ENTRY_KEYS = ("id", "type", CONNECTIONS_KEY)
"""Keys an entry model owns, rather than inlining from the options type."""

_FRAMEWORK_CONNECTION_KEYS = ("reconnect_attempts", "reconnect_period")
"""Keys of a connection's block read by its `Supervisor`, not the connection."""


@dataclasses.dataclass(frozen=True)
class _RegisteredClass:
    cls: type[Controller]
    options_arg: str | None
    options_type: Any
    connection_args: dict[str, type[Connection]]


_ENTRY_REGISTRY: dict[type[BaseModel], _RegisteredClass] = {}
"""Maps each dynamically-built Entry model class to its originating
Controller class, the name and type of its options argument (if any), and its
connection-typed arguments. Populated by ``_build_entry_model`` and read by
``_instantiate_controllers``."""

_CONNECTION_REGISTRY: dict[type[BaseModel], type[Connection]] = {}
"""Maps each dynamically-built connection model class to its Connection class.
Populated by ``_build_connection_model`` and read by ``_build_supervisor``."""


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
            __init__ method taking no more than one options argument, plus any
            number of arguments hinted with a `Connection` type - those are
            configured under the entry's ``connections:`` block, one key per
            argument name. The chosen class for each id is selected by a
            required ``type`` discriminator in the config.
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
    """Type discriminator used in fastcs.yaml under a ``type:`` key.

    Defaults to ``<top-level-package>.<ClassName>`` so that classes from
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

        controllers, supervisors = _instantiate_controllers(
            instance_options.controllers
        )

        instance = FastCS(
            controllers,
            instance_options.transport,
            loop=asyncio.get_event_loop(),
            supervisors=supervisors,
        )

        instance.run()

    return launch_typer


def _instantiate_controllers(
    controllers_options: list[Any],
) -> tuple[list[Controller], list[list[Supervisor]]]:
    """Instantiate each entry under `controllers:` and seed its path.

    Each item in ``controllers_options`` is a dynamically-built Pydantic
    model that exposes ``id``, ``type``, a ``connections`` block if the
    controller takes any connections, and the controller's options fields
    inlined as siblings. The originating Controller class and its arguments
    are looked up in ``_ENTRY_REGISTRY`` (populated by ``_build_entry_model``).
    The entry's ``id`` is seeded into the controller's ``_path`` via
    ``set_path([id])`` so that ``ControllerAPI.path`` is rooted at the YAML id.

    Every connection is created, and given a `Supervisor`, before the controller
    is constructed: the controller receives each supervisor's handle as the
    argument of the same name. Nothing is opened yet.

    Returns:
        The controllers, and the supervisors of each entry - which is the whole
        set of connections the runner will supervise.

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
    supervisors: list[list[Supervisor]] = []
    for entry in controllers_options:
        entry_cls: type[BaseModel] = type(entry)
        registered = _ENTRY_REGISTRY[entry_cls]

        kwargs: dict[str, Any] = {}
        entry_supervisors: list[Supervisor] = []
        for name in registered.connection_args:
            config = getattr(getattr(entry, CONNECTIONS_KEY), name)
            supervisor = _build_supervisor(f"{entry.id}.{name}", config)
            entry_supervisors.append(supervisor)
            kwargs[name] = supervisor.handle

        if registered.options_arg is not None:
            field_values = {
                name: getattr(entry, name)
                for name in entry_cls.model_fields
                if name not in _ENTRY_KEYS
            }
            kwargs[registered.options_arg] = registered.options_type(**field_values)

        controller = registered.cls(**kwargs)
        controller.set_path([entry.id])
        controllers.append(controller)
        supervisors.append(entry_supervisors)
    return controllers, supervisors


def _build_supervisor(name: str, config: BaseModel) -> Supervisor:
    """Create one connection from its block, and the supervisor that owns it."""
    connection_class = _CONNECTION_REGISTRY[type(config)]
    settings = {
        field: getattr(config, field)
        for field in type(config).model_fields
        if field not in _FRAMEWORK_CONNECTION_KEYS
    }
    return Supervisor(
        connection_class(**settings),
        name=name,
        reconnect_attempts=getattr(config, "reconnect_attempts"),  # noqa: B009
        reconnect_period=getattr(config, "reconnect_period"),  # noqa: B009
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


def _parameter_default(parameter: inspect.Parameter) -> Any:
    return ... if parameter.default is inspect.Parameter.empty else parameter.default


def _connection_field_definitions(
    connection_class: type[Connection],
) -> dict[str, tuple[Any, Any]]:
    """Field-by-field definitions for one connection, from its ``__init__``.

    A connection's settings are constructor arguments rather than an options
    object, so its config block is built from the signature directly - the same
    treatment `_options_field_definitions` gives an options type.
    """
    if connection_class.__init__ is object.__init__:
        return {}

    signature = inspect.signature(connection_class.__init__)
    hints = get_type_hints(connection_class.__init__)

    fields: dict[str, tuple[Any, Any]] = {}
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue

        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise LaunchError(
                f"Cannot build config for {connection_class.__name__}: "
                f"`{parameter}` cannot be expressed as a config field."
            )

        if name not in hints:
            raise LaunchError(
                f"Expected typehinting in '{connection_class.__name__}"
                f".__init__' but received {signature}. Add a typehint for `{name}`."
            )
        fields[name] = (hints[name], _parameter_default(parameter))

    return fields


_CONNECTION_MODELS: dict[type[Connection], type[BaseModel]] = {}
"""One config model per Connection class, however many entries use it."""


def _build_connection_model(connection_class: type[Connection]) -> type[BaseModel]:
    """Build a Pydantic model for one connection in an entry's ``connections:``.

    The connection's own settings, plus the framework keys its `Supervisor` reads.
    There is no ``type:`` - the controller's type hint supplies it - and no
    ``depends_on``, which is declared on the Connection class.
    """
    if connection_class in _CONNECTION_MODELS:
        return _CONNECTION_MODELS[connection_class]

    fields: dict[str, Any] = {
        "reconnect_attempts": (int, Field(DEFAULT_RECONNECT_ATTEMPTS, ge=1)),
        "reconnect_period": (float, Field(DEFAULT_RECONNECT_PERIOD, gt=0)),
    }
    for name, definition in _connection_field_definitions(connection_class).items():
        if name in fields:
            raise LaunchError(
                f"Connection {connection_class.__name__} takes a {name!r} argument, "
                f"which collides with a launch-framework key."
            )
        fields[name] = definition

    connection_model = create_model(
        f"{connection_class.__name__}Config",
        __config__={"extra": "forbid"},
        **fields,
    )
    _CONNECTION_REGISTRY[connection_model] = connection_class
    _CONNECTION_MODELS[connection_class] = connection_model
    return connection_model


def _connections_model(
    controller_class: type[Controller], connection_args: dict[str, type[Connection]]
) -> type[BaseModel]:
    """The ``connections:`` block of an entry: one required key per argument."""
    fields: dict[str, Any] = {
        name: (_build_connection_model(connection_class), ...)
        for name, connection_class in connection_args.items()
    }
    return create_model(
        f"{controller_class.__name__}Connections",
        __config__={"extra": "forbid"},
        **fields,
    )


def _is_connection_type(hint: Any) -> bool:
    return isinstance(hint, type) and issubclass(hint, Connection)


def _build_entry_model(controller_class: type[Controller]) -> type[BaseModel]:
    """Build a Pydantic model for one entry under `controllers:`.

    Each entry exposes ``id``, a ``type`` discriminator literal and - when the
    controller takes any connections - a ``connections`` block, alongside the
    options-type's fields inlined as siblings (no nested ``controller:`` block).
    The Controller class and its arguments are recorded in ``_ENTRY_REGISTRY``
    for use by ``_instantiate_controllers``.

    An ``__init__`` argument hinted with a `Connection` type is a connection, and
    its name is its key under ``connections:``. At most one other argument is
    allowed, and it is the options type.
    """
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)
    names = [*args.args[1:], *args.kwonlyargs]
    hints = get_type_hints(controller_class.__init__)
    hints.pop("return", None)
    discriminator = _discriminator(controller_class)

    connection_args = {
        name: hints[name] for name in names if _is_connection_type(hints.get(name))
    }
    options_args = [name for name in names if name not in connection_args]

    fields: dict[str, Any] = {
        "id": (str, ...),
        "type": (Literal[discriminator], ...),
    }
    if connection_args:
        fields[CONNECTIONS_KEY] = (
            _connections_model(controller_class, connection_args),
            ...,
        )

    options_arg: str | None = None
    options_type: Any = None

    if len(options_args) == 1:
        options_arg = options_args[0]
        if options_arg not in hints:
            raise LaunchError(
                f"Expected typehinting in '{controller_class.__name__}"
                f".__init__' but received {sig}. Add a typehint for `{options_arg}`."
            )
        options_type = hints[options_arg]
        options_fields = _options_field_definitions(options_type)
        for reserved in _ENTRY_KEYS:
            if reserved in options_fields:
                raise LaunchError(
                    f"Options type {options_type.__name__} for "
                    f"{controller_class.__name__} declares a {reserved!r} field, "
                    f"which collides with a launch-framework key."
                )
        fields.update(options_fields)
    elif len(options_args) > 1:
        raise LaunchError(
            f"Expected no more than one options argument for "
            f"'{controller_class.__name__}.__init__', besides its connections, "
            f"but received {len(options_args)} as `{sig}`"
        )

    entry_model = create_model(
        f"{controller_class.__name__}Entry",
        __config__={"extra": "forbid"},
        **fields,
    )
    _ENTRY_REGISTRY[entry_model] = _RegisteredClass(
        cls=controller_class,
        options_arg=options_arg,
        options_type=options_type,
        connection_args=connection_args,
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
