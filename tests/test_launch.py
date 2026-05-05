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
from fastcs.controllers import Controller
from fastcs.datatypes import Int
from fastcs.exceptions import LaunchError
from fastcs.launch import (
    _build_options_model,
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
    read = AttrR(Int())

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


runner = CliRunner()


def test_single_arg_schema():
    entry_model = create_model(
        "SingleArgEntry",
        __config__={"extra": "forbid"},
        type=(Literal["SingleArg"], "SingleArg"),
    )
    target_model = create_model(
        "SingleArg",
        __config__={"extra": "forbid"},
        controllers=(dict[str, entry_model], ...),
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
        type=(Literal["IsHinted"], "IsHinted"),
        controller=(SomeConfig, ...),
    )
    target_model = create_model(
        "IsHinted",
        __config__={"extra": "forbid"},
        controllers=(dict[str, entry_model], ...),
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
        return_value={"controller": "test"},
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


def _controllers(instance) -> dict:
    """Read the dynamically-defined `controllers` mapping off a validated model."""
    return instance.controllers  # type: ignore[attr-defined]


def test_single_class_omits_type():
    """Single-class registration may omit `type:` under each controller entry."""
    options_model = _build_options_model([IsHinted])
    instance = options_model.model_validate(
        {
            "controllers": {"my-id": {"controller": {"name": "x"}}},
            "transport": [{"rest": {}}],
        }
    )
    entry = _controllers(instance)["my-id"]
    assert entry.type == "IsHinted"
    assert entry.controller.name == "x"


def test_multi_class_discriminator():
    """Multi-class registration uses `type:` to pick the matching entry."""
    options_model = _build_options_model([IsHinted, OtherHinted])
    instance = options_model.model_validate(
        {
            "controllers": {
                "first": {"type": "IsHinted", "controller": {"name": "a"}},
                "second": {"type": "OtherHinted", "controller": {"address": "b"}},
            },
            "transport": [{"rest": {}}],
        }
    )

    first = _controllers(instance)["first"]
    second = _controllers(instance)["second"]
    assert first.type == "IsHinted"
    assert isinstance(first.controller, SomeConfig)
    assert second.type == "OtherHinted"
    assert isinstance(second.controller, OtherConfig)


def test_multi_class_unknown_type_rejected():
    options_model = _build_options_model([IsHinted, OtherHinted])
    with pytest.raises(ValidationError):
        options_model.model_validate(
            {
                "controllers": {"x": {"type": "Unknown", "controller": {"name": "a"}}},
                "transport": [{"rest": {}}],
            }
        )


def test_type_name_override():
    """`type_name: ClassVar[str]` overrides the default `__name__` discriminator."""
    options_model = _build_options_model([Aliased, OtherHinted])
    instance = options_model.model_validate(
        {
            "controllers": {
                "x": {"type": "aliased-controller", "controller": {"name": "n"}},
            },
            "transport": [{"rest": {}}],
        }
    )
    assert _controllers(instance)["x"].type == "aliased-controller"


def test_duplicate_id_rejected_at_yaml_load(tmp_path):
    """ruamel YAML rejects duplicate mapping keys, so duplicate ids cannot
    survive parsing; this is the natural source of duplicate-id rejection."""
    cfg = tmp_path / "dup.yaml"
    cfg.write_text(
        "controllers:\n"
        "  same:\n"
        "    controller: {name: a}\n"
        "  same:\n"
        "    controller: {name: b}\n"
        "transport:\n"
        "  - rest: {}\n"
    )
    yaml = YAML(typ="safe")
    with pytest.raises(Exception, match="duplicate key"):
        yaml.load(cfg)


def test_multi_controller_run_errors_until_fastcs_supports_list(
    mocker: MockerFixture, tmp_path
):
    """Until FastCS itself takes list[Controller] in the next slice, running a
    multi-entry config raises a clear LaunchError."""
    mocker.patch("fastcs.launch.FastCS.run")
    cfg = tmp_path / "multi.yaml"
    cfg.write_text(
        "controllers:\n"
        "  one:\n"
        "    type: IsHinted\n"
        "    controller: {name: a}\n"
        "  two:\n"
        "    type: OtherHinted\n"
        "    controller: {address: b}\n"
        "transport:\n"
        "  - rest: {}\n"
    )
    app = _launch([IsHinted, OtherHinted])
    result = runner.invoke(app, ["run", str(cfg)])
    assert isinstance(result.exception, LaunchError)
    assert "Multi-controller execution" in str(result.exception)
