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
from fastcs.connections import Connection, Connections, HTTPConnection
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
    def __init__(self, settings: LinkSettings, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    async def connect(self) -> None: ...

    async def close(self) -> None: ...


class OtherConnection(Connection):
    type_name: ClassVar[str] = "other-connection"

    def __init__(self, label: str = "unlabelled", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label

    async def connect(self) -> None: ...

    async def close(self) -> None: ...


class NeedsConnections(Controller):
    """The common shape: a registry, and nothing else."""

    def __init__(self, connections: Connections) -> None:
        super().__init__()
        self.claimed = connections.get("link", FakeConnection)
        self.registry = connections


class NeedsBoth(Controller):
    """`connections` alongside an options object - the two-argument case."""

    def __init__(self, connections: Connections, arg: SomeConfig) -> None:
        super().__init__()
        self.registry = connections
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
        "Expected no more than 2 arguments for 'ManyArgs.__init__' "
        "but received 3 as `(self, arg: tests.test_launch.SomeConfig, too_many)`"
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


def _build(controllers: list[dict], classes=None, connections=None) -> list[Controller]:
    """Validate a `controllers:` list and instantiate it, as ``run`` does."""
    return _build_with_registries(controllers, classes, connections)[0]


def _build_with_registries(
    controllers: list[dict], classes=None, connections=None
) -> tuple[list[Controller], list[Connections]]:
    """As `_build`, but also the registries the launcher hands the runner."""
    options_model = _build_options_model(
        classes or [NeedsConnections], connections or [FakeConnection]
    )
    instance = options_model.model_validate(
        {"controllers": controllers, "transport": [{"rest": {}}]}
    )
    return _instantiate_controllers(_controllers(instance))


def test_connections_are_declared_per_entry():
    """The key is the *role* the driver asks for; the entry identifies the
    instance. Both entries claim "link" and get different objects - which a
    single global block could not express."""
    first, second = _build(
        [
            {
                "id": "PITCH",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "192.168.0.1"},
                    }
                },
            },
            {
                "id": "YAW",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "192.168.0.2", "port": 23},
                        "reconnect_period": 5.0,
                    }
                },
            },
        ]
    )

    assert isinstance(first, NeedsConnections) and isinstance(second, NeedsConnections)
    assert first.claimed is not second.claimed
    assert first.claimed.settings == LinkSettings(host="192.168.0.1", port=22)
    assert second.claimed.settings == LinkSettings(host="192.168.0.2", port=23)
    # Forwarded to `Connection` through the connection's own **kwargs
    assert second.claimed.reconnect_period == 5.0


def test_connections_alongside_an_options_object():
    """`connections` does not count towards the argument limit, so a controller
    may take it *and* an options object."""
    (controller,) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsBoth",
                "name": "a-name",
                "connections": {
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                    }
                },
            }
        ],
        classes=[NeedsBoth],
    )

    assert isinstance(controller, NeedsBoth)
    assert controller.arg == SomeConfig(name="a-name")
    assert len(controller.registry) == 1


@pytest.mark.parametrize("declared", ["ssh", ["ssh"]])
def test_depends_on_takes_a_name_or_a_list(declared):
    (controller,) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                        "depends_on": declared,
                    },
                    "ssh": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                    },
                },
            }
        ]
    )

    assert isinstance(controller, NeedsConnections)
    ssh = controller.registry.get("ssh", FakeConnection)
    assert controller.claimed.depends_on == [ssh]


def test_depends_on_resolves_several_names():
    (controller,) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {
                    "ssh": {"type": "tests.FakeConnection", "settings": {"host": "h"}},
                    "status": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                    },
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                        "depends_on": ["ssh", "status"],
                    },
                },
            }
        ]
    )

    assert isinstance(controller, NeedsConnections)
    assert [type(c).__name__ for c in controller.claimed.depends_on] == [
        "FakeConnection",
        "FakeConnection",
    ]
    assert controller.claimed.depends_on == [
        controller.registry.get("ssh", FakeConnection),
        controller.registry.get("status", FakeConnection),
    ]


def test_unknown_depends_on_name_lists_the_declared_roles():
    with pytest.raises(LaunchError) as error:
        _build(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {
                        "link": {
                            "type": "tests.FakeConnection",
                            "settings": {"host": "h"},
                            "depends_on": "typo",
                        }
                    },
                }
            ]
        )

    assert "depends on 'typo', which is not declared" in str(error.value)
    assert "Declared: ['link']" in str(error.value)


def test_depends_on_cycle_is_a_config_error():
    """Caught while the roles still have names, rather than deadlocking in the
    reconnect tasks at runtime."""
    with pytest.raises(LaunchError, match="Cycle in `depends_on`"):
        _build(
            [
                {
                    "id": "x",
                    "type": "tests.NeedsConnections",
                    "connections": {
                        "link": {
                            "type": "tests.FakeConnection",
                            "settings": {"host": "h"},
                            "depends_on": "ssh",
                        },
                        "ssh": {
                            "type": "tests.FakeConnection",
                            "settings": {"host": "h"},
                            "depends_on": "link",
                        },
                    },
                }
            ]
        )


def test_connections_for_a_controller_that_cannot_receive_them():
    with pytest.raises(LaunchError, match="no `connections` argument"):
        _build(
            [
                {
                    "id": "x",
                    "type": "tests.IsHinted",
                    "name": "n",
                    "connections": {
                        "link": {
                            "type": "tests.FakeConnection",
                            "settings": {"host": "h"},
                        }
                    },
                }
            ],
            classes=[IsHinted],
        )


def test_connections_is_a_reserved_options_field():
    """Reserved whether or not any Connection classes are registered, so that
    registering one later cannot collide with an existing driver."""

    @dataclass
    class Colliding:
        connections: str

    class Collides(Controller):
        def __init__(self, arg: Colliding) -> None:
            super().__init__()

    with pytest.raises(LaunchError, match="'connections' field"):
        _build_options_model([Collides])


def test_connection_type_discriminates_within_the_entry():
    (controller,) = _build(
        [
            {
                "id": "x",
                "type": "tests.NeedsConnections",
                "connections": {
                    "link": {
                        "type": "tests.FakeConnection",
                        "settings": {"host": "h"},
                    },
                    "other": {"type": "other-connection", "label": "labelled"},
                },
            }
        ],
        connections=[FakeConnection, OtherConnection],
    )

    assert isinstance(controller, NeedsConnections)
    assert controller.registry.get("other", OtherConnection).label == "labelled"


def test_unknown_connection_type_rejected():
    options_model = _build_options_model([NeedsConnections], [FakeConnection])
    with pytest.raises(ValidationError):
        options_model.model_validate(
            {
                "controllers": [
                    {
                        "id": "x",
                        "type": "tests.NeedsConnections",
                        "connections": {"link": {"type": "not.AConnection"}},
                    }
                ],
                "transport": [{"rest": {}}],
            }
        )


def test_no_connections_block_without_registered_classes():
    """Nothing could be declared, so the key is not in the schema at all."""
    schema = get_controller_schema(NeedsConnections)
    entry = schema["$defs"]["NeedsConnectionsEntry"]
    assert "connections" not in entry["properties"]


def test_a_single_connection_class_need_not_be_a_list():
    schema = get_controller_schema(NeedsConnections, [FakeConnection])
    assert "FakeConnectionConfig" in schema["$defs"]


def test_a_connection_argument_without_a_type_hint():
    class Unhinted(Connection):
        def __init__(self, settings) -> None:
            super().__init__()

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    with pytest.raises(LaunchError, match="Add a typehint for `settings`"):
        _build_options_model([NeedsConnections], [Unhinted])


def test_a_connection_taking_star_args():
    class Starred(Connection):
        def __init__(self, *settings: str) -> None:
            super().__init__()

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    with pytest.raises(LaunchError, match=r"`\*settings` cannot be expressed"):
        _build_options_model([NeedsConnections], [Starred])


def test_a_connection_argument_colliding_with_a_framework_key():
    class Colliding(Connection):
        def __init__(self, type: str) -> None:  # noqa: A002
            super().__init__()

        async def connect(self) -> None: ...

        async def close(self) -> None: ...

    with pytest.raises(LaunchError, match="collides with a launch-framework key"):
        _build_options_model([NeedsConnections], [Colliding])


def test_the_framework_http_connection_is_usable_from_config():
    """A REST driver names `fastcs.HTTPConnection` and writes no connection at all."""

    class NeedsHTTP(Controller):
        def __init__(self, connections: Connections) -> None:
            super().__init__()
            self.connection = connections.get("link", HTTPConnection)

    controllers = _build(
        [
            {
                "id": "OD",
                "type": "tests.NeedsHTTP",
                "connections": {
                    "link": {
                        "type": "fastcs.HTTPConnection",
                        "settings": {"host": "odin", "port": 8888},
                        "reconnect_period": 5.0,
                    }
                },
            }
        ],
        classes=[NeedsHTTP],
        connections=[HTTPConnection],
    )

    connection = controllers[0].connection
    assert isinstance(connection, HTTPConnection)
    assert connection._settings.base_url == "http://odin:8888"
    assert connection.reconnect_period == 5.0


def test_connections_block_in_the_schema():
    schema = get_controller_schema(NeedsConnections, FakeConnection)
    entry = schema["$defs"]["NeedsConnectionsEntry"]
    assert entry["properties"]["connections"]["additionalProperties"] == {
        "$ref": "#/$defs/FakeConnectionConfig"
    }

    connection = schema["$defs"]["FakeConnectionConfig"]
    assert connection["properties"]["type"]["const"] == "tests.FakeConnection"
    # Forwarded `**kwargs` stand in for `Connection`'s own arguments
    assert "reconnect_period" in connection["properties"]
    assert "reconnect_attempts" in connection["properties"]
    # Resolved after the block is built, so it is names here rather than objects
    assert "depends_on" in connection["properties"]
