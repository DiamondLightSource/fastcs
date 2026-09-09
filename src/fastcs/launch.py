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
from fastcs.connections import Connection, Connections
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
"""Reserved, at both entry level in the config and in a Controller's ``__init__``.

The block under an entry declares that controller's connections; the parameter of
the same name is how the registry built from it reaches the controller. Reserved
whether or not any connection classes are registered, so that registering one
later cannot collide with an existing driver's options field.
"""

_ENTRY_KEYS = ("id", "type", CONNECTIONS_KEY)
"""Keys an entry model owns, rather than inlining from the options type."""


@dataclasses.dataclass(frozen=True)
class _RegisteredClass:
    cls: type[Controller]
    expects_options: bool
    options_type: Any
    expects_connections: bool = False


_ENTRY_REGISTRY: dict[type[BaseModel], _RegisteredClass] = {}
"""Maps each dynamically-built Entry model class to its originating
Controller class and whether it expects an options arg (with the
options-type, if any) and a `Connections` registry. Populated by
``_build_entry_model`` and read by ``_instantiate_controllers``."""

_CONNECTION_REGISTRY: dict[type[BaseModel], type[Connection]] = {}
"""Maps each dynamically-built connection model class to its Connection class.
Populated by ``_build_connection_model`` and read by ``_build_connections``."""


def launch(
    controller_classes: type[Controller] | list[type[Controller]],
    version: str | None = None,
    connection_classes: type[Connection] | list[type[Connection]] | None = None,
) -> None:
    """
    Serves as an entry point for starting FastCS applications.

    By utilizing type hints in each Controller's __init__ method, this
    function provides a command-line interface to describe and gather the
    required configuration before instantiating the application.

    Args:
        controller_classes: One or more FastCS Controller classes to make
            available for instantiation. Each must have a type-hinted
            __init__ method taking an optional ``connections`` argument and
            no more than one options argument. The chosen class for each id
            is selected by a required ``type`` discriminator in the config.
        version (Optional[str]): The version of the FastCS application.
        connection_classes: The `Connection` classes an entry's
            ``connections:`` block may declare, handed over explicitly like
            the Controller classes rather than discovered from a global
            registry. Omit it and no entry may declare connections.

    Raises:
        LaunchError: If a class's __init__ is not as expected.

    Typical usage:
        if __name__ == "__main__":
            launch(MyController)            # single class
            launch([MyControllerA, MyControllerB])  # multi-class
            launch(MyController, connection_classes=[MyConnection])
    """
    _launch(controller_classes, version, connection_classes)()


def _normalise_classes(
    controller_classes: type[Controller] | list[type[Controller]],
) -> list[type[Controller]]:
    if isinstance(controller_classes, list):
        if not controller_classes:
            raise LaunchError("launch() requires at least one Controller class")
        return controller_classes
    return [controller_classes]


def _normalise_connection_classes(
    connection_classes: type[Connection] | list[type[Connection]] | None,
) -> list[type[Connection]]:
    if connection_classes is None:
        return []
    if isinstance(connection_classes, list):
        return connection_classes
    return [connection_classes]


def _discriminator(controller_class: type[Controller] | type[Connection]) -> str:
    """Type discriminator used in fastcs.yaml under a ``type:`` key.

    Defaults to ``<top-level-package>.<ClassName>`` so that classes from
    independently-distributed packages cannot collide. May be overridden
    verbatim (no prefix added) by setting ``type_name: ClassVar[str]`` on
    the Controller or Connection class.
    """
    top_level_package = controller_class.__module__.split(".", 1)[0]
    default = f"{top_level_package}.{controller_class.__name__}"
    return getattr(controller_class, "type_name", default)


def _launch(
    controller_classes: type[Controller] | list[type[Controller]],
    version: str | None = None,
    connection_classes: type[Connection] | list[type[Connection]] | None = None,
) -> typer.Typer:
    classes = _normalise_classes(controller_classes)
    fastcs_options = _build_options_model(
        classes, _normalise_connection_classes(connection_classes)
    )
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
    """Instantiate each entry under `controllers:` and seed its path.

    Each item in ``controllers_options`` is a dynamically-built Pydantic
    model that exposes ``id``, ``type``, an optional ``connections`` block
    and the controller's options fields inlined as siblings. The originating
    Controller class and its options-type are looked up in
    ``_ENTRY_REGISTRY`` (populated by ``_build_entry_model``). The entry's
    ``id`` is seeded into the controller's ``_path`` via ``set_path([id])``
    so that ``ControllerAPI.path`` is rooted at the YAML id.

    One `Connections` registry is built per entry, from that entry's own
    block, and forwarded down its subtree. Role names are therefore local to
    an entry: two motors can both claim ``"motor"`` and get different
    objects, which a single global block could not express.
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

        block = getattr(entry, CONNECTIONS_KEY, {})
        args: list[Any] = []

        if registered.expects_connections:
            args.append(_build_connections(entry.id, block))
        elif block:
            raise LaunchError(
                f"Controller {entry.id!r} declares connections "
                f"{sorted(block)}, but {registered.cls.__name__}.__init__ takes "
                f"no `{CONNECTIONS_KEY}` argument, so nothing would receive them."
            )

        if registered.expects_options:
            field_values = {
                name: getattr(entry, name)
                for name in entry_cls.model_fields
                if name not in _ENTRY_KEYS
            }
            args.append(registered.options_type(**field_values))

        controller = registered.cls(*args)
        controller.set_path([entry.id])
        controllers.append(controller)
    return controllers


def _depends_on_names(declared: str | list[str]) -> list[str]:
    """``depends_on`` as a list of names, whether one or several were given."""
    return [declared] if isinstance(declared, str) else list(declared)


def _build_connections(entry_id: str, block: dict[str, Any]) -> Connections:
    """Build one entry's `Connections` registry from its ``connections:`` block.

    Every connection is constructed first and ``depends_on`` resolved afterwards,
    because it names connections by role and none of them exist while the block is
    being read. Unknown names and cycles are rejected here rather than at startup,
    so a config typo is a config error naming the roles it could have meant.
    """
    instances: dict[str, Connection] = {}
    dependency_names: dict[str, list[str]] = {}

    for name, options in block.items():
        connection_class = _CONNECTION_REGISTRY[type(options)]
        kwargs = {
            field: getattr(options, field)
            for field in type(options).model_fields
            if field not in ("type", "depends_on")
        }
        instances[name] = connection_class(**kwargs)
        dependency_names[name] = _depends_on_names(options.depends_on)

    _check_dependency_names(entry_id, dependency_names)

    for name, dependencies in dependency_names.items():
        instances[name].depends_on = [instances[each] for each in dependencies]

    return Connections(instances)


def _check_dependency_names(
    entry_id: str, dependency_names: dict[str, list[str]]
) -> None:
    """Reject an unknown ``depends_on`` name, and any cycle between them.

    A cycle deadlocks silently at runtime - every connection in it waits on the
    others forever, saying nothing beyond "waiting on dependencies" - so it is
    caught here, where the roles have names to put in the message.
    """
    for name, dependencies in dependency_names.items():
        for dependency in dependencies:
            if dependency not in dependency_names:
                raise LaunchError(
                    f"Connection {name!r} in controller {entry_id!r} depends on "
                    f"{dependency!r}, which is not declared. Declared: "
                    f"{sorted(dependency_names)}"
                )

    visiting: list[str] = []
    settled: set[str] = set()

    def visit(name: str) -> None:
        if name in settled:
            return
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name) :], name])
            raise LaunchError(
                f"Cycle in `depends_on` for controller {entry_id!r}: {cycle}. "
                "Every connection in a cycle waits on the others forever."
            )

        visiting.append(name)
        for dependency in dependency_names[name]:
            visit(dependency)
        visiting.pop()
        settled.add(name)

    for name in dependency_names:
        visit(name)


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
    treatment `_options_field_definitions` gives an options type. ``**kwargs``
    forwarded to `Connection` stands in for that base class's own arguments, so a
    connection that only forwards them still gets ``reconnect_period`` and
    ``max_attempts`` in its schema.

    ``depends_on`` is deliberately absent: it names other connections, which do
    not exist while the block is being read, so `_build_connections` resolves it
    once they all do.
    """
    signature = inspect.signature(connection_class.__init__)
    hints = get_type_hints(connection_class.__init__)
    base_signature = inspect.signature(Connection.__init__)
    base_hints = get_type_hints(Connection.__init__)

    fields: dict[str, tuple[Any, Any]] = {}
    for name, parameter in signature.parameters.items():
        if name in ("self", "depends_on"):
            continue

        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise LaunchError(
                f"Cannot build config for {connection_class.__name__}: "
                f"`*{name}` cannot be expressed as a config field."
            )

        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            for base_name, base_parameter in base_signature.parameters.items():
                if base_name in ("self", "depends_on") or base_name in fields:
                    continue
                fields[base_name] = (
                    base_hints[base_name],
                    _parameter_default(base_parameter),
                )
            continue

        if name not in hints:
            raise LaunchError(
                f"Expected typehinting in '{connection_class.__name__}"
                f".__init__' but received {signature}. Add a typehint for `{name}`."
            )
        fields[name] = (hints[name], _parameter_default(parameter))

    return fields


def _build_connection_model(connection_class: type[Connection]) -> type[BaseModel]:
    """Build a Pydantic model for one connection in an entry's ``connections:``."""
    discriminator = _discriminator(connection_class)

    fields: dict[str, Any] = {
        "type": (Literal[discriminator], ...),
        "depends_on": (Union[str, list[str]], Field(default_factory=list)),  # noqa: UP007
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
    return connection_model


def _connections_field(
    connection_classes: list[type[Connection]],
) -> tuple[Any, Any]:
    """The ``connections:`` field of an entry: role name -> connection config.

    A discriminated union over the registered Connection classes, following the
    Controller pattern rather than the Transport one - the classes are handed to
    `launch` explicitly, so there is no global ``Connection.subclasses`` and no new
    public surface.
    """
    models = [_build_connection_model(cls) for cls in connection_classes]

    if len(models) == 1:
        value_type: Any = models[0]
    else:
        value_type = Annotated[
            Union[tuple(models)], Field(discriminator="type")  # noqa: UP007
        ]

    return (dict[str, value_type], Field(default_factory=dict))


def _build_entry_model(
    controller_class: type[Controller],
    connection_classes: list[type[Connection]],
) -> type[BaseModel]:
    """Build a Pydantic model for one entry under `controllers:`.

    Each entry exposes ``id``, a ``type`` discriminator literal and - when any
    Connection classes are registered - a ``connections`` block, alongside the
    options-type's fields inlined as siblings (no nested ``controller:`` block).
    The Controller class and its options-type are recorded in ``_ENTRY_REGISTRY``
    for use by ``_instantiate_controllers``.

    ``connections`` is a reserved parameter name: a Controller that takes one gets
    the registry built from its own block, and it counts towards neither the
    argument limit nor the inlined option fields.
    """
    sig = inspect.signature(controller_class.__init__)
    args = inspect.getfullargspec(controller_class.__init__)[0]
    discriminator = _discriminator(controller_class)

    expects_connections = CONNECTIONS_KEY in args
    counted = [arg for arg in args if arg != CONNECTIONS_KEY]

    fields: dict[str, Any] = {
        "id": (str, ...),
        "type": (Literal[discriminator], ...),
    }
    if connection_classes:
        fields[CONNECTIONS_KEY] = _connections_field(connection_classes)

    expects_options = False
    options_type: Any = None

    if len(counted) == 1:
        pass
    elif len(counted) == 2:
        expects_options = True
        options_arg = counted[-1]
        hints = get_type_hints(controller_class.__init__)
        hints.pop("return", None)
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
    else:
        raise LaunchError(
            f"Expected no more than 2 arguments for '{controller_class.__name__}"
            f".__init__' but received {len(counted)} as `{sig}`"
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
        expects_connections=expects_connections,
    )
    return entry_model


def _build_options_model(
    controller_classes: list[type[Controller]],
    connection_classes: list[type[Connection]] | None = None,
) -> type[BaseModel]:
    """Build the top-level Pydantic model for fastcs.yaml.

    `controllers:` is a list of entries. Each entry is either the single
    registered class's entry model or a discriminated union over all
    registered classes; in both cases the entry's ``id:`` and ``type:``
    fields are required. Duplicate ``id`` values across the list are
    rejected by ``_instantiate_controllers``.
    """
    connection_classes = connection_classes or []
    entries = [
        _build_entry_model(cls, connection_classes) for cls in controller_classes
    ]

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
    connection_classes: type[Connection] | list[type[Connection]] | None = None,
) -> dict[str, Any]:
    """Gets schema for given controller class(es) for serialisation."""
    options_model = _build_options_model(
        _normalise_classes(target), _normalise_connection_classes(connection_classes)
    )
    return options_model.model_json_schema()
