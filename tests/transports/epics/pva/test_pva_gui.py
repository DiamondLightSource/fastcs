import numpy as np
import pytest
from pvi.device import (
    LED,
    ButtonPanel,
    CheckBox,
    ImageRead,
    SignalR,
    SignalW,
    SignalX,
    TableRead,
    TableWrite,
    TextFormat,
    TextRead,
    TextWrite,
)

from fastcs.attributes import AttrR, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Table, Waveform
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.pva.gui import PvaEpicsGUI


@pytest.mark.parametrize(
    "datatype, widget",
    [
        (Waveform(array_dtype=np.int32), ImageRead()),
    ],
)
def test_pva_get_attribute_component_r(datatype, widget):
    gui = EpicsGUI(ControllerAPI())

    assert gui._get_attribute_component(["DEVICE"], "Attr", AttrR(datatype)) == SignalR(
        name="Attr", read_pv="DEVICE:Attr", read_widget=widget
    )


def test_get_pv_in_pva():
    gui = PvaEpicsGUI(ControllerAPI())

    assert gui._get_pv(["DEVICE"], "A") == "pva://DEVICE:A"
    assert gui._get_pv(["DEVICE", "B"], "C") == "pva://DEVICE:B:C"
    assert gui._get_pv(["DEVICE", "D", "E"], "F") == "pva://DEVICE:D:E:F"


def test_get_attribute_component_table_write():
    gui = PvaEpicsGUI(ControllerAPI())

    attribute_component = gui._get_attribute_component(
        ["DEVICE"],
        "Table",
        AttrW(
            Table(
                structured_dtype=[
                    ("FIELD1", np.uint32),
                    ("FIELD2", np.bool),
                    ("FIELD3", np.dtype("S1000")),
                ]
            )
        ),
    )

    assert isinstance(attribute_component, SignalW)
    assert isinstance(attribute_component.write_widget, TableWrite)
    assert attribute_component.write_widget.widgets == [
        TextWrite(precision=0),
        CheckBox(),
        TextWrite(format=TextFormat.string),
    ]


def test_get_attribute_component_table_read():
    gui = PvaEpicsGUI(ControllerAPI())

    attribute_component = gui._get_attribute_component(
        ["DEVICE"],
        "Table",
        AttrR(
            Table(
                structured_dtype=[
                    ("FIELD1", np.uint32),
                    ("FIELD2", np.bool),
                    ("FIELD3", np.dtype("S1000")),
                ]
            )
        ),
    )

    assert isinstance(attribute_component, SignalR)
    assert isinstance(attribute_component.read_widget, TableRead)
    assert attribute_component.read_widget.widgets == [
        TextRead(precision=0),
        LED(),
        TextRead(format=TextFormat.string),
    ]


def test_get_command_component():
    gui = PvaEpicsGUI(ControllerAPI())

    component = gui._get_command_component(["DEVICE"], "Command")

    assert isinstance(component, SignalX)
    assert component.write_widget == ButtonPanel(actions={"Command": "true"})
