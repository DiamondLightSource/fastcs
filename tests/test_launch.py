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
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
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
    read = AttrR(int)

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
