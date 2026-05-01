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
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Bool, Enum, Float, Int, String, Waveform
from fastcs.transports.epics.gui import EpicsGUI


def test_get_pv():
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    assert gui._get_pv([], "A") == "DEVICE:A"
    assert gui._get_pv(["B"], "C") == "DEVICE:B:C"
    assert gui._get_pv(["D", "E"], "F") == "DEVICE:D:E:F"


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
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    assert gui._get_attribute_component([], "Attr", AttrR(datatype)) == SignalR(
        name="Attr", read_pv="Attr", read_widget=widget
    )


@pytest.mark.parametrize(
    "datatype",
    [
        (Waveform(array_dtype=np.int32, shape=(10, 10))),
    ],
)
def test_get_attribute_component_r_signal_none(datatype):
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    assert gui._get_attribute_component([], "Attr", AttrR(datatype)) is None


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
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    assert gui._get_attribute_component([], "Attr", AttrW(datatype)) == SignalW(
        name="Attr", write_pv="Attr", write_widget=widget
    )


def test_get_attribute_component_none(mocker):
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    mocker.patch.object(gui, "_get_read_widget", return_value=None)
    mocker.patch.object(gui, "_get_write_widget", return_value=None)
    assert gui._get_attribute_component([], "Attr", AttrR(Int())) is None
    assert gui._get_attribute_component([], "Attr", AttrW(Int())) is None
    assert gui._get_attribute_component([], "Attr", AttrRW(Int())) is None


def test_get_write_widget_none():
    gui = EpicsGUI(ControllerAPI(), "DEVICE")
    assert (
        gui._get_write_widget(attribute=AttrR(Waveform(array_dtype=np.int32))) is None
    )


def test_get_components(controller_api):
    gui = EpicsGUI(controller_api, "DEVICE")

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
    gui = EpicsGUI(controller_api, "DEVICE")
    mocker.patch.object(gui, "_get_attribute_component", return_value=None)

    components = gui.extract_api_components(controller_api)

    assert components == []


def test_get_command_component():
    gui = EpicsGUI(ControllerAPI(), "DEVICE")

    component = gui._get_command_component([], "Command")

    assert isinstance(component, SignalX)
    assert component.write_widget == ButtonPanel(actions={"Command": "1"})
