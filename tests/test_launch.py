import json
import os
from dataclasses import dataclass
from typing import ClassVar, Literal

import pytest
from pydantic import ValidationError, create_model
from pytest_mock import MockerFixture
from ruamel.yaml import YAML
from typer.testing import CliRunner

from fastcs import __version__
from fastcs.attributes import AttrR
from fastcs.connections import (
    DEFAULT_RECONNECT_ATTEMPTS,
    Connection,
    HTTPConnection,
    Supervisor,
)
from fastcs.connections.supervisor import connection_of, supervisor_of
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.exceptions import LaunchError
from fastcs.launch import (
    _build_options_model,
    _instantiate_controllers,
    _launch,
    get_controller_schema,
    launch,
)
from fastcs.transports import Transport


@dataclass
class SomeConfig:
    name: str


@dataclass
class OtherConfig:
    address: str


class SingleArg(Controller):
    def __init__(self):
        super().__init__()


class NotHinted(Controller):
    def __init__(self, arg):
        super().__init__()


class IsHinted(Controller):
    read: AttrR[int]

    def __init__(self, arg: SomeConfig) -> None:
        super().__init__()


class ManyArgs(Controller):
    def __init__(self, arg: SomeConfig, too_many):
        super().__init__()


class OtherHinted(Controller):
    def __init__(self, arg: OtherConfig) -> None:
        super().__init__()


class Aliased(Controller):
    type_name: ClassVar[str] = "aliased-controller"

    def __init__(self, arg: SomeConfig) -> None:
        super().__init__()


@dataclass
class LinkSettings:
    host: str
    port: int = 22


class FakeConnection(Connection):
    def __init__(self, settings: LinkSettings) -> None:
        self.settings = settings

    async def connect(self) -> None: ...

    async def close(self) -> None: ...


class OtherConnection(Connection):
    def __init__(self, tag: str = "untagged") -> None:
        self.tag = tag

    async def connect(self) -> None: ...

    async def close(self) -> None: ...


class NeedsConnections(Controller):
    """The common shape: connections declared by type hints, and nothing else."""

    def __init__(self, link: FakeConnection, other: OtherConnection) -> None:
        super().__init__()
        self.link = link
        self.other = other


class NeedsBoth(Controller):
    """A connection alongside an options object."""

    def __init__(self, link: FakeConnection, arg: SomeConfig) -> None:
        super().__init__()
        self.link = link
        self.arg = arg


runner = CliRunner()


def test_single_arg_schema():
    entry_model = create_model(
        "SingleArgEntry",
        __config__={"extra": "forbid"},
        id=(str, ...),
        type=(Literal["tests.SingleArg"], ...),
    )
    target_model = create_model(
        "SingleArg",
        __config__={"extra": "forbid"},
        controllers=(list[entry_model], ...),
        transport=(list[Transport.union()], ...),
    )
    target_dict = target_model.model_json_schema()

    app = _launch(SingleArg)
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    result_dict = json.loads(result.stdout)

    assert result_dict == target_dict


def test_is_hinted_schema(data):
    entry_model = create_model(
        "IsHintedEntry",
        __config__={"extra": "forbid"},
        id=(str, ...),
        type=(Literal["tests.IsHinted"], ...),
        name=(str, ...),
    )
    target_model = create_model(
        "IsHinted",
        __config__={"extra": "forbid"},
        controllers=(list[entry_model], ...),
        transport=(list[Transport.union()], ...),
    )
    target_dict = target_model.model_json_schema()

    app = _launch(IsHinted)
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    result_dict = json.loads(result.stdout)

    assert result_dict == target_dict


def test_not_hinted_schema():
    error = (
        "Expected typehinting in 'NotHinted.__init__' but received "
        "(self, arg). Add a typehint for `arg`."
    )

    with pytest.raises(LaunchError) as exc_info:
        launch(NotHinted)
    assert str(exc_info.value) == error


def test_over_defined_schema():
    error = (
        ""
        "Expected no more than one options argument for 'ManyArgs.__init__', "
        "besides its connections, but received 2 as "
        "`(self, arg: tests.test_launch.SomeConfig, too_many)`"
    )

    with pytest.raises(LaunchError) as exc_info:
        launch(ManyArgs)
    assert str(exc_info.value) == error


def test_version():
    impl_version = "0.0.1"
    expected = f"SingleArg: {impl_version}\nFastCS: {__version__}\n"
    app = _launch(SingleArg, version=impl_version)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == expected


def test_no_version():
    expected = f"FastCS: {__version__}\n"
    app = _launch(SingleArg)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == expected


def test_launch(mocker: MockerFixture, data):
    run = mocker.patch("fastcs.launch.FastCS.run")

    app = _launch(IsHinted)
    result = runner.invoke(app, ["run", str(data / "config.yaml")])
    assert result.exit_code == 0

    run.assert_called_once()


def test_get_schema(data):
    target_schema = get_controller_schema(IsHinted)
    if os.environ.get("FASTCS_REGENERATE_OUTPUT", None):
        with open(data / "schema.json", "w") as f:
            json.dump(target_schema, f, indent=2)

    ref_schema = YAML(typ="safe").load(data / "schema.json")
    assert target_schema == ref_schema


def test_error_if_identical_context_in_transports(mocker: MockerFixture, data):
    mocker.patch(
        "fastcs.transports.Transport.context",
        new_callable=mocker.PropertyMock,
        return_value={"controllers": "test"},
    )
    mocker.patch(
        "fastcs.transports.epics.pva.transport.EpicsPVATransport.serve",
        new_callable=mocker.PropertyMock,
    )
    mocker.patch(
        "fastcs.transports.epics.ca.transport.EpicsCATransport.serve",
        new_callable=mocker.PropertyMock,
    )
    app = _launch(IsHinted)
    result = runner.invoke(app, ["run", str(data / "config.yaml")])
    assert isinstance(result.exception, RuntimeError)
    assert "Duplicate context keys found" in result.exception.args[0]


def _controllers(instance) -> list:
    """Read the dynamically-defined `controllers` list off a validated model."""
    return instance.controllers  # type: ignore[attr-defined]


def test_single_class_requires_type():
    """`type:` is mandatory on every controllers entry, even when only one
    Controller class is registered."""
    options_model = _build_options_model([IsHinted])
    with pytest.raises(ValidationError):
        options_model.model_validate(
            {
                "controllers": [{"id": "my-id", "name": "x"}],
                "transport": [{"rest": {}}],
            }
        )

    instance = options_model.model_validate(
        {
            "controllers": [{"id": "my-id", "type": "tests.IsHinted", "name": "x"}],
            "transport": [{"rest": {}}],
        }
    )
    entry = _controllers(instance)[0]
    assert entry.id == "my-id"
    assert entry.type == "tests.IsHinted"
    assert entry.name == "x"


def test_multi_class_discriminator():
    """Multi-class registration uses `type:` to pick the matching entry."""
    options_model = _build_options_model([IsHinted, OtherHinted])
    instance = options_model.model_validate(
        {
            "controllers": [
                {"id": "first", "type": "tests.IsHinted", "name": "a"},
                {"id": "second", "type": "tests.OtherHinted", "address": "b"},
            ],
            "transport": [{"rest": {}}],
        }
    )

    first, second = _controllers(instance)
    assert first.id == "first"
    assert first.type == "tests.IsHinted"
    assert first.name == "a"
    assert second.id == "second"
    assert second.type == "tests.OtherHinted"
    assert second.address == "b"


def test_multi_class_unknown_type_rejected():
    options_model = _build_options_model([IsHinted, OtherHinted])
    with pytest.raises(ValidationError):
        options_model.model_validate(
            {
                "controllers": [{"id": "x", "type": "Unknown", "name": "a"}],
                "transport": [{"rest": {}}],
            }
        )


def test_type_name_override():
    """`type_name: ClassVar[str]` overrides the default `<pkg>.<ClassName>`
    discriminator (and is used verbatim — no package prefix is added)."""
    options_model = _build_options_model([Aliased, OtherHinted])
    instance = options_model.model_validate(
        {
            "controllers": [
                {"id": "x", "type": "aliased-controller", "name": "n"},
            ],
            "transport": [{"rest": {}}],
        }
    )
    assert _controllers(instance)[0].type == "aliased-controller"


def test_duplicate_id_rejected_at_run(mocker: MockerFixture, tmp_path):
    """Two entries with the same `id` are rejected at run time. (List-form
    YAML accepts duplicate ids syntactically, so the launcher checks.)"""
    mocker.patch("fastcs.launch.FastCS.run")
    cfg = tmp_path / "dup.yaml"
    cfg.write_text(
        "controllers:\n"
        "  - id: same\n"
        "    type: tests.IsHinted\n"
        "    name: a\n"
        "  - id: same\n"
        "    type: tests.IsHinted\n"
        "    name: b\n"
        "transport:\n"
        "  - rest: {}\n"
    )
    app = _launch(IsHinted)
    result = runner.invoke(app, ["run", str(cfg)])
    assert isinstance(result.exception, LaunchError)
    assert "Duplicate controller id" in str(result.exception)


def test_multi_controller_run_reaches_fastcs(mocker: MockerFixture, tmp_path):
    """Multi-entry config is wired through FastCS, which receives both
    controllers in the order they appear under `controllers:`."""
    init_spy = mocker.spy(FastCS, "__init__")
    mocker.patch("fastcs.launch.FastCS.run")
    cfg = tmp_path / "multi.yaml"
    cfg.write_text(
        "controllers:\n"
        "  - id: one\n"
        "    type: tests.IsHinted\n"
        "    name: a\n"
        "  - id: two\n"
        "    type: tests.OtherHinted\n"
        "    address: b\n"
        "transport:\n"
        "  - rest: {}\n"
    )
    app = _launch([IsHinted, OtherHinted])
    result = runner.invoke(app, ["run", str(cfg)])
    assert result.exit_code == 0, result.output
    init_spy.assert_called_once()
    controllers_arg = init_spy.call_args.args[1]
    assert [c.path[0] for c in controllers_arg] == ["one", "two"]
    assert [type(c) for c in controllers_arg] == [IsHinted, OtherHinted]


# `connections:` in the config, and injection

LINK = {"settings": {"host": "h"}}


def _build(
    controllers: list[dict], classes=None
) -> tuple[list[Controller], list[list[Supervisor]]]:
    """Validate a `controllers:` list and instantiate it, as ``run`` does."""
    options_model = _build_options_model(classes or [NeedsConnections])
    instance = options_model.model_validate(
        {"controllers": controllers, "transport": [{"rest": {}}]}
    )
    return _instantiate_controllers(_controllers(instance))


def _validate(controllers: list[dict], classes=None) -> None:
    options_model = _build_options_model(classes or [NeedsConnections])
    options_model.model_validate(
        {"controllers": controllers, "transport": [{"rest": {}}]}
    )


def test_a_controller_receives_a_handle_for_each_connection():
    (controllers, _) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            }
        ]
    )
    (controller,) = controllers
    assert isinstance(controller, NeedsConnections)

    assert supervisor_of(controller.link) is not None


def test_a_handle_passes_as_the_hinted_type():
    (controllers, _) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            }
        ]
    )
    (controller,) = controllers
    assert isinstance(controller, NeedsConnections)

    assert isinstance(controller.link, FakeConnection)


def test_a_connection_is_built_from_its_settings():
    (controllers, _) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {"settings": {"host": "192.168.0.2", "port": 23}},
                    "other": {"tag": "tagged"},
                },
            }
        ]
    )
    (controller,) = controllers
    assert isinstance(controller, NeedsConnections)

    assert controller.link.settings == LinkSettings(host="192.168.0.2", port=23)


def test_the_supervisors_of_each_entry_are_returned_in_argument_order():
    (controllers, supervisors) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {"other": {}, "link": LINK},
            }
        ]
    )
    (controller,) = controllers
    assert isinstance(controller, NeedsConnections)

    assert supervisors == [
        [supervisor_of(controller.link), supervisor_of(controller.other)]
    ]


def test_a_supervisor_is_named_after_its_entry_and_argument():
    (_, supervisors) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            }
        ]
    )

    assert [s.name for s in supervisors[0]] == ["x.link", "x.other"]


def test_reconnect_settings_are_read_by_the_supervisor():
    (_, supervisors) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {**LINK, "reconnect_attempts": 5, "reconnect_period": 2.0},
                    "other": {},
                },
            }
        ]
    )
    link = supervisors[0][0]

    assert (link.reconnect_attempts, link.reconnect_period) == (5, 2.0)


def test_reconnect_settings_default_per_connection():
    (_, supervisors) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            }
        ]
    )

    assert supervisors[0][0].reconnect_attempts == DEFAULT_RECONNECT_ATTEMPTS


def test_connections_are_created_per_entry():
    """The same class used twice in one config gets two independent links."""
    (controllers, _) = _build(
        [
            {
                "id": "PITCH",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            },
            {
                "id": "YAW",
                "type": "tests.NeedsConnections",
                "connections": {"link": LINK, "other": {}},
            },
        ]
    )
    first, second = controllers
    assert isinstance(first, NeedsConnections)
    assert isinstance(second, NeedsConnections)

    assert connection_of(first.link) is not connection_of(second.link)


def test_connections_alongside_an_options_object():
    (controllers, _) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsBoth",
                "name": "a-name",
                "connections": {"link": LINK},
            }
        ],
        classes=[NeedsBoth],
    )
    (controller,) = controllers
    assert isinstance(controller, NeedsBoth)

    assert controller.arg == SomeConfig(name="a-name")


def test_every_connection_is_required():
    with pytest.raises(ValidationError, match="other"):
        _validate(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {"link": LINK},
                }
            ]
        )


def test_an_undeclared_connection_name_is_rejected():
    with pytest.raises(ValidationError, match="typo"):
        _validate(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {"link": LINK, "other": {}, "typo": {}},
                }
            ]
        )


def test_an_unknown_connection_setting_is_rejected():
    with pytest.raises(ValidationError, match="depends_on"):
        _validate(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {
                        "link": {**LINK, "depends_on": "other"},
                        "other": {},
                    },
                }
            ]
        )


def test_a_connection_type_is_not_configurable():
    """The type hint supplies it."""
    with pytest.raises(ValidationError, match="type"):
        _validate(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {
                        "link": {**LINK, "type": "tests.FakeConnection"},
                        "other": {},
                    },
                }
            ]
        )


def test_connections_for_a_controller_that_takes_none():
    with pytest.raises(ValidationError, match="connections"):
        _validate(
            [
                {
                    "id": "x",
                    "type": "tests.IsHinted",
                    "name": "n",
                    "connections": {"link": LINK},
                }
            ],
            classes=[IsHinted],
        )


def test_connections_is_a_reserved_options_field():
    @dataclass
    class Colliding:
        connections: str

    class Collides(Controller):
        def __init__(self, arg: Colliding) -> None:
            super().__init__()

    with pytest.raises(LaunchError, match="'connections' field"):
        _build_options_model([Collides])


def test_no_connections_block_for_a_controller_that_takes_none():
    schema = get_controller_schema(IsHinted)
    entry = schema["$defs"]["IsHintedEntry"]
    assert "connections" not in entry["properties"]


def test_the_connections_block_in_the_schema_names_each_argument():
    schema = get_controller_schema(NeedsConnections)
    block = schema["$defs"]["NeedsConnectionsConnections"]

    assert block["required"] == ["link", "other"]


def test_the_framework_keys_are_in_a_connections_schema():
    schema = get_controller_schema(NeedsConnections)
    connection = schema["$defs"]["FakeConnectionConfig"]

    assert {"reconnect_attempts", "reconnect_period"} <= set(connection["properties"])


def test_a_connection_argument_without_a_type_hint():
    class Unhinted(Connection):
        def __init__(self, settings) -> None: ...

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    class NeedsUnhinted(Controller):
        def __init__(self, link: Unhinted) -> None:
            super().__init__()

    with pytest.raises(LaunchError, match="Add a typehint for `settings`"):
        _build_options_model([NeedsUnhinted])


def test_a_connection_taking_star_args():
    class Starred(Connection):
        def __init__(self, *settings: str) -> None: ...

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    class NeedsStarred(Controller):
        def __init__(self, link: Starred) -> None:
            super().__init__()

    with pytest.raises(LaunchError, match=r"`\*settings: str` cannot be expressed"):
        _build_options_model([NeedsStarred])


def test_a_connection_argument_colliding_with_a_framework_key():
    class Colliding(Connection):
        def __init__(self, reconnect_period: float) -> None: ...

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    class NeedsColliding(Controller):
        def __init__(self, link: Colliding) -> None:
            super().__init__()

    with pytest.raises(LaunchError, match="collides with a launch-framework key"):
        _build_options_model([NeedsColliding])


def test_a_connection_with_no_constructor_has_only_framework_settings():
    class Bare(Connection):
        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    class NeedsBare(Controller):
        def __init__(self, link: Bare) -> None:
            super().__init__()

    schema = get_controller_schema(NeedsBare)

    assert set(schema["$defs"]["BareConfig"]["properties"]) == {
        "reconnect_attempts",
        "reconnect_period",
    }


def test_the_framework_http_connection_is_usable_from_config():
    """A REST driver hints `HTTPConnection` and writes no connection at all."""

    class NeedsHTTP(Controller):
        def __init__(self, odin: HTTPConnection) -> None:
            super().__init__()
            self.connection = odin

    (controllers, _) = _build(
        [
            {
                "id": "OD",
                "type": "tests.NeedsHTTP",
                "connections": {"odin": {"settings": {"host": "odin", "port": 8888}}},
            }
        ],
        classes=[NeedsHTTP],
    )

    connection = connection_of(controllers[0].connection)
    assert isinstance(connection, HTTPConnection)
    assert connection._settings.base_url == "http://odin:8888"
