"""D4 unit tests for per-controller GUI / docs file emission (#358)."""

from pathlib import Path

import pytest

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Int
from fastcs.transports.epics.emission import (
    DOCS_EXT,
    INDEX_STEM,
    emit_docs_files,
    emit_gui_files,
)
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.options import (
    EpicsDocsOptions,
    EpicsGUIFormat,
    EpicsGUIOptions,
)
from fastcs.transports.epics.pva.gui import PvaEpicsGUI


class _Alpha(Controller):
    foo = AttrR(Int())


class _Beta(Controller):
    bar = AttrR(Int())


def _api_with_id(controller_class: type[Controller], id: str):
    controller = controller_class()
    controller.set_id(id)
    api, _, _ = controller.create_api_and_tasks()
    return api


@pytest.fixture
def two_apis():
    return [_api_with_id(_Alpha, "alpha"), _api_with_id(_Beta, "beta")]


@pytest.fixture
def one_api():
    return [_api_with_id(_Alpha, "alpha")]


# --- GUI emission ----------------------------------------------------------


def test_gui_emits_per_controller_files_and_index(two_apis, tmp_path: Path):
    options = EpicsGUIOptions(output_dir=tmp_path)
    emit_gui_files(two_apis, options, EpicsGUI)

    assert (tmp_path / "alpha.bob").is_file()
    assert (tmp_path / "beta.bob").is_file()
    assert (tmp_path / f"{INDEX_STEM}.bob").is_file()


def test_gui_emits_index_even_for_single_controller(one_api, tmp_path: Path):
    """Stable file layout: index is emitted regardless of controller count."""
    emit_gui_files(one_api, EpicsGUIOptions(output_dir=tmp_path), EpicsGUI)

    assert (tmp_path / "alpha.bob").is_file()
    assert (tmp_path / f"{INDEX_STEM}.bob").is_file()


def test_gui_index_lists_controllers_in_declaration_order(two_apis, tmp_path: Path):
    """User story 23: index buttons follow YAML declaration order."""
    emit_gui_files(two_apis, EpicsGUIOptions(output_dir=tmp_path), EpicsGUI)

    text = (tmp_path / f"{INDEX_STEM}.bob").read_text()
    alpha_pos = text.index("alpha.bob")
    beta_pos = text.index("beta.bob")
    assert alpha_pos < beta_pos


def test_gui_index_reverses_when_apis_reversed(two_apis, tmp_path: Path):
    """If declaration order flips, the index order flips too."""
    emit_gui_files(
        list(reversed(two_apis)),
        EpicsGUIOptions(output_dir=tmp_path),
        EpicsGUI,
    )

    text = (tmp_path / f"{INDEX_STEM}.bob").read_text()
    assert text.index("beta.bob") < text.index("alpha.bob")


def test_gui_creates_missing_output_dir(two_apis, tmp_path: Path):
    nested = tmp_path / "deep" / "opis"
    emit_gui_files(two_apis, EpicsGUIOptions(output_dir=nested), EpicsGUI)

    assert (nested / "alpha.bob").is_file()
    assert (nested / f"{INDEX_STEM}.bob").is_file()


def test_gui_index_tolerates_digit_leading_id(tmp_path: Path):
    """pvi's index DeviceRef requires PascalStr; ids starting with a digit
    (e.g. UUID-flavoured test prefixes) get coerced safely instead of
    blowing up at validation time."""
    api = _api_with_id(_Alpha, "120e1b648d9a4ec8a2ae2bf33cf3e1ee")
    emit_gui_files([api], EpicsGUIOptions(output_dir=tmp_path), EpicsGUI)

    assert (tmp_path / "120e1b648d9a4ec8a2ae2bf33cf3e1ee.bob").is_file()
    assert (tmp_path / f"{INDEX_STEM}.bob").is_file()


def test_gui_uses_pva_builder_for_pva_transport(two_apis, tmp_path: Path):
    """PVA transport's GUI builder writes ``pva://`` PV prefixes."""
    emit_gui_files(two_apis, EpicsGUIOptions(output_dir=tmp_path), PvaEpicsGUI)

    assert "pva://alpha:Foo" in (tmp_path / "alpha.bob").read_text()
    assert "pva://beta:Bar" in (tmp_path / "beta.bob").read_text()


def test_gui_respects_file_format(two_apis, tmp_path: Path):
    """The configured file format propagates to per-controller and index files."""
    options = EpicsGUIOptions(output_dir=tmp_path, file_format=EpicsGUIFormat.bob)
    emit_gui_files(two_apis, options, EpicsGUI)

    assert (tmp_path / "alpha.bob").is_file()
    assert (tmp_path / f"{INDEX_STEM}.bob").is_file()


# --- docs emission ---------------------------------------------------------


def test_docs_emits_per_controller_files_and_index(two_apis, tmp_path: Path):
    emit_docs_files(two_apis, EpicsDocsOptions(output_dir=tmp_path))

    assert (tmp_path / f"alpha{DOCS_EXT}").is_file()
    assert (tmp_path / f"beta{DOCS_EXT}").is_file()
    assert (tmp_path / f"{INDEX_STEM}{DOCS_EXT}").is_file()


def test_docs_emits_index_even_for_single_controller(one_api, tmp_path: Path):
    emit_docs_files(one_api, EpicsDocsOptions(output_dir=tmp_path))

    assert (tmp_path / f"alpha{DOCS_EXT}").is_file()
    assert (tmp_path / f"{INDEX_STEM}{DOCS_EXT}").is_file()


def test_docs_index_lists_controllers_in_declaration_order(two_apis, tmp_path: Path):
    emit_docs_files(two_apis, EpicsDocsOptions(output_dir=tmp_path))

    index_text = (tmp_path / f"{INDEX_STEM}{DOCS_EXT}").read_text()
    assert index_text.index("alpha") < index_text.index("beta")


def test_docs_per_controller_file_lists_attributes(two_apis, tmp_path: Path):
    emit_docs_files(two_apis, EpicsDocsOptions(output_dir=tmp_path))

    assert "foo" in (tmp_path / f"alpha{DOCS_EXT}").read_text()
    assert "bar" in (tmp_path / f"beta{DOCS_EXT}").read_text()


def test_docs_creates_missing_output_dir(two_apis, tmp_path: Path):
    nested = tmp_path / "deep" / "reference"
    emit_docs_files(two_apis, EpicsDocsOptions(output_dir=nested))

    assert (nested / f"alpha{DOCS_EXT}").is_file()
    assert (nested / f"{INDEX_STEM}{DOCS_EXT}").is_file()
