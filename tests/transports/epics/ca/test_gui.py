from pathlib import Path

import numpy as np
import pytest
from pvi.device import (
    LED,
    ArrayTrace,
    ButtonPanel,
    ComboBox,
    Group,
    SignalR,
    SignalRW,
    SignalW,
    SignalX,
    SubScreen,
    TextFormat,
    TextRead,
    TextWrite,
    ToggleButton,
)
from tests.util import ColourEnum

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import Bool, Enum, Float, Int, String, Waveform
from fastcs.transports.epics.emission import INDEX_STEM, emit_gui_files
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.options import EpicsGUIOptions


def test_get_pv():
    gui = EpicsGUI(ControllerAPI())

    assert gui._get_pv(["DEVICE"], "A") == "DEVICE:A"
    assert gui._get_pv(["DEVICE", "B"], "C") == "DEVICE:B:C"
    assert gui._get_pv(["DEVICE", "D", "E"], "F") == "DEVICE:D:E:F"


@pytest.mark.parametrize(
    "datatype, widget",
    [
        (Bool(), LED()),
        (Int(), TextRead()),
        (Float(), TextRead()),
        (String(), TextRead(format=TextFormat.string)),
        (Enum(ColourEnum), TextRead(format=TextFormat.string)),
        (Waveform(array_dtype=np.int32), ArrayTrace(axis="x")),
    ],
)
def test_get_attribute_component_r(datatype, widget):
    gui = EpicsGUI(ControllerAPI())

    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrR(datatype)) == SignalR(
        name="Attr", read_pv="DEVICE:Attr", read_widget=widget
    )


@pytest.mark.parametrize(
    "datatype",
    [
        (Waveform(array_dtype=np.int32, shape=(10, 10))),
    ],
)
def test_get_attribute_component_r_signal_none(datatype):
    gui = EpicsGUI(ControllerAPI())

    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrR(datatype)) is None


@pytest.mark.parametrize(
    "datatype, widget",
    [
        (Bool(), ToggleButton()),
        (Int(), TextWrite()),
        (Float(), TextWrite()),
        (String(), TextWrite(format=TextFormat.string)),
        (Enum(ColourEnum), ComboBox(choices=["RED", "GREEN", "BLUE"])),
    ],
)
def test_get_attribute_component_w(datatype, widget):
    gui = EpicsGUI(ControllerAPI())

    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrW(datatype)) == SignalW(
        name="Attr", write_pv="DEVICE:Attr", write_widget=widget
    )


def test_get_attribute_component_none(mocker):
    gui = EpicsGUI(ControllerAPI())

    mocker.patch.object(gui, "_get_read_widget", return_value=None)
    mocker.patch.object(gui, "_get_write_widget", return_value=None)
    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrR(Int())) is None
    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrW(Int())) is None
    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrRW(Int())) is None


def test_get_write_widget_none():
    gui = EpicsGUI(ControllerAPI())
    assert (
        gui._get_write_widget(attribute=AttrR(Waveform(array_dtype=np.int32))) is None
    )


def test_get_components(controller):
    controller_api = controller._build_api(["DEVICE"])
    gui = EpicsGUI(controller_api)

    components = gui.extract_api_components(controller_api)
    assert components == [
        Group(
            name="SubController01",
            layout=SubScreen(labelled=True),
            children=[
                SignalR(
                    name="ReadInt",
                    read_pv="DEVICE:SubController01:ReadInt",
                    read_widget=TextRead(),
                )
            ],
        ),
        Group(
            name="SubController02",
            layout=SubScreen(labelled=True),
            children=[
                SignalR(
                    name="ReadInt",
                    read_pv="DEVICE:SubController02:ReadInt",
                    read_widget=TextRead(),
                )
            ],
        ),
        SignalR(
            name="ReadInt",
            read_pv="DEVICE:ReadInt",
            read_widget=TextRead(),
        ),
        SignalRW(
            name="ReadWriteInt",
            write_pv="DEVICE:ReadWriteInt",
            write_widget=TextWrite(),
            read_pv="DEVICE:ReadWriteInt_RBV",
            read_widget=TextRead(),
        ),
        SignalRW(
            name="ReadWriteFloat",
            write_pv="DEVICE:ReadWriteFloat",
            write_widget=TextWrite(),
            read_pv="DEVICE:ReadWriteFloat_RBV",
            read_widget=TextRead(),
        ),
        SignalR(
            name="ReadBool",
            read_pv="DEVICE:ReadBool",
            read_widget=LED(),
        ),
        SignalW(
            name="WriteBool",
            write_pv="DEVICE:WriteBool",
            write_widget=ToggleButton(),
        ),
        SignalRW(
            name="ReadString",
            read_pv="DEVICE:ReadString_RBV",
            write_pv="DEVICE:ReadString",
        ),
        SignalX(
            name="Go",
            write_pv="DEVICE:Go",
            write_widget=ButtonPanel(actions={"Go": "1"}),
            value="1",
        ),
    ]


def test_get_components_none(mocker):
    """Test that if _get_attribute_component returns none it is skipped"""

    controller_api = ControllerAPI()
    gui = EpicsGUI(controller_api)
    mocker.patch.object(gui, "_get_attribute_component", return_value=None)

    components = gui.extract_api_components(controller_api)

    assert components == []


def test_get_command_component():
    gui = EpicsGUI(ControllerAPI())

    component = gui._get_command_component(["DEVICE"], "Command")

    assert isinstance(component, SignalX)
    assert component.write_widget == ButtonPanel(actions={"Command": "1"})


class _A(Controller):
    foo = AttrR(Int())


class _B(Controller):
    bar = AttrR(Int())


def _api_with_id(cls, name):
    c = cls(name)
    api, _, _ = c.create_api_and_tasks()
    return api


def test_emit_gui_writes_index_alongside_per_controller_files(tmp_path: Path):
    """#358 acceptance criteria: per-id .bob files and an index file."""
    apis = [_api_with_id(_A, "alpha"), _api_with_id(_B, "beta")]

    emit_gui_files(apis, EpicsGUIOptions(output_dir=tmp_path), EpicsGUI)

    assert (tmp_path / "alpha.bob").is_file()
    assert (tmp_path / "beta.bob").is_file()
    assert (tmp_path / f"{INDEX_STEM}.bob").is_file()
