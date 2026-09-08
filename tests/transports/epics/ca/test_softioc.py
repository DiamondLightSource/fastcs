import enum
import re
from typing import Any

import numpy as np
import pytest
from pytest_mock import MockerFixture
from softioc import softioc
from tests.assertable_controller import (
    AssertableControllerAPI,
    MyTestController,
)
from tests.util import ColourEnum

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import Array1D, Limits, Meta, NumericLimits
from fastcs.exceptions import FastCSError
from fastcs.methods import Command
from fastcs.transports.epics.ca import EpicsCATransport
from fastcs.transports.epics.ca.ioc import (
    EpicsCAIOC,
    _add_alias,
    _add_attr_pvi_info,
    _add_pvi_info,
    _add_sub_controller_pvi_info,
    _create_and_link_command_pv,
    _create_and_link_read_pv,
    _create_and_link_write_pv,
)
from fastcs.transports.epics.ca.util import (
    _make_in_record,
    _make_out_record,
)
from fastcs.transports.epics.util import EPICS_MAX_NAME_LENGTH

DEVICE = "DEVICE"

SEVENTEEN_VALUES = [str(i) for i in range(1, 18)]


class OnOffStates(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


@pytest.mark.asyncio
async def test_create_and_link_read_pv(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")
    add_attr_pvi_info = mocker.patch(
        "fastcs.transports.epics.ca.ioc._add_attr_pvi_info"
    )
    record = make_record.return_value

    attribute = AttrR(int)
    attribute.add_readback_callback = mocker.MagicMock()

    _create_and_link_read_pv("PREFIX", "PV", "attr", None, attribute)

    make_record.assert_called_once_with("PREFIX:PV", attribute)
    add_attr_pvi_info.assert_called_once_with(record, "PREFIX", "attr", "r")

    # Extract the callback generated and set in the function and call it
    attribute.add_readback_callback.assert_called_once_with(mocker.ANY)
    record_set_callback = attribute.add_readback_callback.call_args[0][0]
    await record_set_callback(1)

    record.set.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_create_and_link_write_pv_adds_alias(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    record = make_record.return_value
    record.add_alias = mocker.MagicMock()
    attribute = mocker.MagicMock()

    _create_and_link_write_pv("PREFIX", "PV", "attr", "alias", attribute)

    make_record.assert_called_once_with("PREFIX:PV", attribute, on_update=mocker.ANY)
    record.add_alias.assert_called_once_with("alias")


@pytest.mark.asyncio
async def test_create_and_link_read_pv_adds_alias(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")
    record = make_record.return_value
    record.add_alias = mocker.MagicMock()
    attribute = mocker.MagicMock()

    _create_and_link_read_pv("PREFIX", "PV_RBV", "attr", "alias", attribute)

    make_record.assert_called_once_with("PREFIX:PV_RBV", attribute)
    record.add_alias.assert_called_once_with("alias")


@pytest.mark.asyncio
async def test_create_and_link_command_pv_adds_alias(mocker: MockerFixture):
    make_action = mocker.patch("fastcs.transports.epics.ca.ioc.builder.Action")
    record = make_action.return_value
    record.add_alias = mocker.MagicMock()
    command = mocker.MagicMock()

    _create_and_link_command_pv("PREFIX", "Command", "command", "alias", command)

    make_action.assert_called_once_with(
        "PREFIX:Command",
        on_update=mocker.ANY,
        blocking=True,
        initial_value=0,
        ZNAM="Idle",
        ONAM="Active",
    )
    record.add_alias.assert_called_once_with("alias")


@pytest.mark.asyncio
async def test_add_alias_skips_alias_if_too_long(mocker: MockerFixture):
    alias_name = "alias"

    # mock EPICS_MAX_NAME_LENGTH such that length of alias is at this maximum
    mocker.patch(
        "fastcs.transports.epics.ca.ioc.EPICS_MAX_NAME_LENGTH",
        len(alias_name),
    )

    # lengthen alias name beyond maximum
    too_long_alias_name = f"long_{alias_name}"

    record = mocker.MagicMock()
    _add_alias(record, alias_name)
    record.add_alias.assert_called_once_with(alias_name)

    _add_alias(record, too_long_alias_name)

    with pytest.raises(AssertionError):
        # assert alias that is too long is not added
        record.add_alias.assert_called_once_with(too_long_alias_name)


@pytest.mark.asyncio
async def test_ioc_raises_if_duplicate_aliases_provided(mocker: MockerFixture):
    aliases = {"A": "Alias", "B": "Alias"}
    with pytest.raises(
        RuntimeError, match=re.escape("duplicate aliases were provided: ['Alias']")
    ):
        EpicsCAIOC(mocker.MagicMock(), aliases)


@pytest.mark.parametrize(
    "attribute,record_type,kwargs",
    (
        (
            AttrR(str),
            "longStringIn",
            {"length": 257, "DESC": None, "initial_value": ""},
        ),
        (
            AttrR(str, length=10),
            "longStringIn",
            {"length": 11, "DESC": None, "initial_value": ""},
        ),
        (
            AttrR(ColourEnum),
            "mbbIn",
            {
                "ZRST": "RED",
                "ONST": "GREEN",
                "TWST": "BLUE",
                "DESC": None,
                "initial_value": 0,
            },
        ),
        (
            AttrR(
                enum.IntEnum(
                    "ONOFF_STATES",
                    {"DISABLED": 0, "ENABLED": 1},
                )
            ),
            "mbbIn",
            {"ZRST": "DISABLED", "ONST": "ENABLED", "DESC": None, "initial_value": 0},
        ),
        (
            AttrR(Array1D[np.int32], shape=(10,)),
            "WaveformIn",
            {
                "DESC": None,
                "length": 10,
            },
        ),
    ),
)
def test_make_input_record(
    attribute: AttrR,
    record_type: str,
    kwargs: dict[str, Any],
    mocker: MockerFixture,
):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")

    pv = "PV"
    _make_in_record(pv, attribute)

    if record_type == "WaveformIn":
        kwargs["initial_value"] = mocker.ANY
    getattr(builder, record_type).assert_called_once_with(
        pv,
        **kwargs,
    )


def _attribute_of_unsupported_datatype(mocker: MockerFixture):
    attribute = mocker.MagicMock()
    attribute.dtype = object
    return attribute


def test_make_record_raises(mocker: MockerFixture):
    mocker.patch("fastcs.transports.epics.ca.util.cast_to_epics_type")
    # An attribute of a datatype EPICS cannot serve, to provoke the fallback
    with pytest.raises(FastCSError):
        _make_in_record("PV", _attribute_of_unsupported_datatype(mocker))


@pytest.mark.asyncio
async def test_create_and_link_write_pv(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    add_attr_pvi_info = mocker.patch(
        "fastcs.transports.epics.ca.ioc._add_attr_pvi_info"
    )
    record = make_record.return_value

    attribute = AttrRW(int)
    attribute.set = mocker.AsyncMock()
    attribute.add_setpoint_callback = mocker.MagicMock()

    _create_and_link_write_pv("PREFIX", "PV", "attr", None, attribute)

    make_record.assert_called_once_with("PREFIX:PV", attribute, on_update=mocker.ANY)
    add_attr_pvi_info.assert_called_once_with(record, "PREFIX", "attr", "w")

    # Extract the setpoint callback generated and set in the function
    attribute.add_setpoint_callback.assert_called_once_with(mocker.ANY)
    set_setpoint_callback = attribute.add_setpoint_callback.call_args[0][0]
    await set_setpoint_callback(1)

    record.set.assert_called_once_with(1, process=False)

    # Unlike the old one-shot seeding, every setpoint change is mirrored.
    record.set.reset_mock()
    await set_setpoint_callback(2)
    record.set.assert_called_once_with(2, process=False)

    # Extract the on update callback generated and set in the function and call it
    on_update_callback = make_record.call_args[1]["on_update"]
    await on_update_callback(1)

    attribute.set.assert_called_once_with(1)


class LongEnum(enum.Enum):
    THIS = 0
    IS = 1
    AN = 2
    ENUM = 3
    WITH = 4
    ALTOGETHER = 5
    TOO = 6
    MANY = 7
    VALUES = 8
    TO = 9
    BE = 10
    DESCRIBED = 11
    BY = 12
    MBB = 14
    TYPE = 15
    EPICS = 16
    RECORDS = 17


@pytest.mark.parametrize(
    "attribute,record_type,kwargs",
    (
        (
            AttrW(enum.IntEnum("ONOFF_STATES", {"DISABLED": 0, "ENABLED": 1})),
            "mbbOut",
            {
                "ZRST": "DISABLED",
                "ONST": "ENABLED",
                "DESC": None,
                "initial_value": 0,
            },
        ),
        (
            AttrW(str),
            "longStringOut",
            {"length": 257, "DESC": None, "initial_value": ""},
        ),
        (
            AttrW(str, length=10),
            "longStringOut",
            {"length": 11, "DESC": None, "initial_value": ""},
        ),
    ),
)
def test_make_output_record(
    attribute: AttrW,
    record_type: str,
    kwargs: dict[str, Any],
    mocker: MockerFixture,
):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    update = mocker.MagicMock()

    pv = "PV"
    _make_out_record(pv, attribute, on_update=update)

    kwargs.update({"always_update": True, "on_update": update, "blocking": True})

    getattr(builder, record_type).assert_called_once_with(
        pv,
        **kwargs,
    )


def test_long_enum_validator(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    update = mocker.MagicMock()
    attribute = AttrRW(LongEnum)
    pv = "PV"
    record = _make_out_record(pv, attribute, on_update=update)
    validator = builder.longStringOut.call_args.kwargs["validate"]
    assert validator(record, "THIS")  # value is one of the Enum names
    assert not validator(record, "an invalid string value")


def test_long_enum_in_creation(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    attribute = AttrR(LongEnum)
    pv = "PV"
    _make_in_record(pv, attribute)
    assert builder.longStringIn.call_args.kwargs["initial_value"] == "THIS"


def test_get_output_record_raises(mocker: MockerFixture):
    mocker.patch("fastcs.transports.epics.ca.util.cast_to_epics_type")
    # An attribute of a datatype EPICS cannot serve, to provoke the fallback
    with pytest.raises(FastCSError):
        _make_out_record(
            "PV",
            _attribute_of_unsupported_datatype(mocker),
            on_update=mocker.MagicMock(),
        )


class EpicsController(MyTestController):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.enum = AttrRW(enum.IntEnum("Enum", {"RED": 0, "GREEN": 1, "BLUE": 2}))
        self.one_d_waveform = AttrRW(Array1D[np.int32], shape=(10,))

    read_int: AttrR[int]
    read_write_int: AttrRW[int]
    read_write_float: AttrRW[float]
    read_bool: AttrR[bool]
    write_bool: AttrW[bool]
    read_string: AttrRW[str]


@pytest.fixture()
def epics_controller_api(class_mocker: MockerFixture):
    return AssertableControllerAPI(EpicsController(), class_mocker, path=[DEVICE])


def test_ioc(mocker: MockerFixture, epics_controller_api: ControllerAPI):
    util_builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    ioc_builder = mocker.patch("fastcs.transports.epics.ca.ioc.builder")
    add_pvi_info = mocker.patch("fastcs.transports.epics.ca.ioc._add_pvi_info")
    add_sub_controller_pvi_info = mocker.patch(
        "fastcs.transports.epics.ca.ioc._add_sub_controller_pvi_info"
    )

    EpicsCAIOC([epics_controller_api], {})

    # Check records are created
    util_builder.boolIn.assert_called_once_with(
        f"{DEVICE}:ReadBool",
        DESC=None,
        ZNAM="False",
        ONAM="True",
        initial_value=False,
    )
    util_builder.longIn.assert_any_call(
        f"{DEVICE}:ReadInt",
        DESC=None,
        EGU=None,
        LOPR=None,
        HOPR=None,
        initial_value=0,
    )
    util_builder.aIn.assert_called_once_with(
        f"{DEVICE}:ReadWriteFloat_RBV",
        DESC=None,
        LOPR=None,
        HOPR=None,
        EGU=None,
        PREC=2,
        initial_value=0.0,
    )
    util_builder.aOut.assert_called_once_with(
        f"{DEVICE}:ReadWriteFloat",
        DESC=None,
        LOPR=None,
        HOPR=None,
        EGU=None,
        PREC=2,
        DRVL=None,
        DRVH=None,
        initial_value=0.0,
        always_update=True,
        blocking=True,
        on_update=mocker.ANY,
    )
    util_builder.longIn.assert_any_call(
        f"{DEVICE}:ReadWriteInt_RBV",
        DESC=None,
        LOPR=None,
        HOPR=None,
        EGU=None,
        initial_value=0,
    )
    util_builder.longOut.assert_called_with(
        f"{DEVICE}:ReadWriteInt",
        LOPR=None,
        HOPR=None,
        EGU=None,
        DRVL=None,
        DRVH=None,
        DESC=None,
        initial_value=0,
        always_update=True,
        blocking=True,
        on_update=mocker.ANY,
    )
    util_builder.mbbIn.assert_called_once_with(
        f"{DEVICE}:Enum_RBV",
        DESC=None,
        initial_value=0,
        ZRST="RED",
        ONST="GREEN",
        TWST="BLUE",
    )
    util_builder.mbbOut.assert_called_once_with(
        f"{DEVICE}:Enum",
        DESC=None,
        initial_value=0,
        ZRST="RED",
        ONST="GREEN",
        TWST="BLUE",
        always_update=True,
        blocking=True,
        on_update=mocker.ANY,
    )
    util_builder.boolOut.assert_called_once_with(
        f"{DEVICE}:WriteBool",
        always_update=True,
        blocking=True,
        on_update=mocker.ANY,
        DESC=None,
        ZNAM="False",
        ONAM="True",
        initial_value=False,
    )
    ioc_builder.Action.assert_any_call(
        f"{DEVICE}:Go",
        on_update=mocker.ANY,
        blocking=True,
        initial_value=0,
        ZNAM="Idle",
        ONAM="Active",
    )

    # Check info tags are added
    add_pvi_info.assert_called_once_with(f"{DEVICE}:PVI")
    add_sub_controller_pvi_info.assert_called_once_with(epics_controller_api)


def test_add_pvi_info(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.ioc.builder")
    controller = mocker.MagicMock()
    controller.path = []
    child = mocker.MagicMock()
    child.path = ["Child"]
    controller.get_sub_controllers.return_value = {"d": child}

    _add_pvi_info(f"{DEVICE}:PVI")

    builder.longStringIn.assert_called_once_with(
        f"{DEVICE}:PVI_PV",
        initial_value=f"{DEVICE}:PVI",
        DESC="The records in this controller",
    )
    record = builder.longStringIn.return_value
    record.add_info.assert_called_once_with(
        "Q:group",
        {
            f"{DEVICE}:PVI": {
                "+id": "epics:nt/NTPVI:1.0",
                "display.description": {"+type": "plain", "+channel": "DESC"},
                "": {"+type": "meta", "+channel": "VAL"},
            }
        },
    )


def test_add_pvi_info_with_parent(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.ioc.builder")
    controller = mocker.MagicMock()
    controller.path = []
    child = mocker.MagicMock()
    child.path = ["Child"]
    controller.get_sub_controllers.return_value = {"d": child}

    child = mocker.MagicMock()
    _add_pvi_info(f"{DEVICE}:Child:PVI", f"{DEVICE}:PVI", "child")

    builder.longStringIn.assert_called_once_with(
        f"{DEVICE}:Child:PVI_PV",
        initial_value=f"{DEVICE}:Child:PVI",
        DESC="The records in this controller",
    )
    record = builder.longStringIn.return_value
    record.add_info.assert_called_once_with(
        "Q:group",
        {
            f"{DEVICE}:Child:PVI": {
                "+id": "epics:nt/NTPVI:1.0",
                "display.description": {"+type": "plain", "+channel": "DESC"},
                "": {"+type": "meta", "+channel": "VAL"},
            },
            f"{DEVICE}:PVI": {
                "value.child.d": {
                    "+channel": "VAL",
                    "+type": "plain",
                    "+trigger": "value.child.d",
                }
            },
        },
    )


def test_add_sub_controller_pvi_info(mocker: MockerFixture):
    add_pvi_info = mocker.patch("fastcs.transports.epics.ca.ioc._add_pvi_info")
    parent_api = mocker.MagicMock()
    parent_api.path = [DEVICE]
    child_api = mocker.MagicMock()
    child_api.path = [DEVICE, "Child"]
    parent_api.sub_apis = {"d": child_api}

    _add_sub_controller_pvi_info(parent_api)

    add_pvi_info.assert_called_once_with(
        f"{DEVICE}:Child:PVI", f"{DEVICE}:PVI", "child"
    )


def test_add_attr_pvi_info(mocker: MockerFixture):
    record = mocker.MagicMock()

    _add_attr_pvi_info(record, DEVICE, "attr", "r")

    record.add_info.assert_called_once_with(
        "Q:group",
        {
            f"{DEVICE}:PVI": {
                "value.attr.r": {
                    "+channel": "NAME",
                    "+type": "plain",
                    "+trigger": "value.attr.r",
                }
            }
        },
    )


async def do_nothing(): ...


class ControllerLongNames(Controller):
    attr_r_with_reallyreallyreallyreallyreallyreallyreally_long_name: AttrR[int]
    attr_rw_with_a_reallyreally_long_name_that_is_too_long_for_rbv: AttrRW[int]
    attr_rw_short_name: AttrRW[int]
    command_with_reallyreallyreallyreallyreallyreallyreally_long_name = Command(
        do_nothing
    )
    command_short_name = Command(do_nothing)


def test_long_pv_names_discarded(mocker: MockerFixture):
    util_builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    ioc_builder = mocker.patch("fastcs.transports.epics.ca.ioc.builder")
    long_name_controller_api = AssertableControllerAPI(
        ControllerLongNames(), mocker, path=[DEVICE]
    )
    long_attr_name = "attr_r_with_reallyreallyreallyreallyreallyreallyreally_long_name"
    long_rw_name = "attr_rw_with_a_reallyreally_long_name_that_is_too_long_for_RBV"
    assert long_name_controller_api.attributes["attr_rw_short_name"].enabled
    assert long_name_controller_api.attributes[long_attr_name].enabled
    EpicsCAIOC([long_name_controller_api], {})
    assert long_name_controller_api.attributes["attr_rw_short_name"].enabled
    assert not long_name_controller_api.attributes[long_attr_name].enabled

    short_pv_name = "attr_rw_short_name".title().replace("_", "")
    util_builder.longOut.assert_called_once_with(
        f"{DEVICE}:{short_pv_name}",
        always_update=True,
        LOPR=None,
        HOPR=None,
        EGU=None,
        DRVL=None,
        DRVH=None,
        blocking=True,
        on_update=mocker.ANY,
        DESC=None,
        initial_value=0,
    )
    util_builder.longIn.assert_called_once_with(
        f"{DEVICE}:{short_pv_name}_RBV",
        DESC=None,
        initial_value=0,
        LOPR=None,
        HOPR=None,
        EGU=None,
    )

    long_pv_name = long_attr_name.title().replace("_", "")
    with pytest.raises(AssertionError):
        util_builder.longIn.assert_called_once_with(f"{DEVICE}:{long_pv_name}")

    long_rw_pv_name = long_rw_name.title().replace("_", "")
    # neither the readback nor setpoint PV gets made if the full pv name with _RBV
    # suffix is too long
    assert (
        EPICS_MAX_NAME_LENGTH - 4
        < len(f"{DEVICE}:{long_rw_pv_name}")
        < EPICS_MAX_NAME_LENGTH
    )

    with pytest.raises(AssertionError):
        util_builder.longOut.assert_called_once_with(
            f"{DEVICE}:{long_rw_pv_name}",
            always_update=True,
            blocking=True,
            on_update=mocker.ANY,
        )
    with pytest.raises(AssertionError):
        util_builder.longIn.assert_called_once_with(f"{DEVICE}:{long_rw_pv_name}_RBV")

    assert long_name_controller_api.command_methods["command_short_name"].enabled
    long_command_name = (
        "command_with_reallyreallyreallyreallyreallyreallyreally_long_name"
    )
    assert not long_name_controller_api.command_methods[long_command_name].enabled

    short_command_pv_name = "command_short_name".title().replace("_", "")
    ioc_builder.Action.assert_called_once_with(
        f"{DEVICE}:{short_command_pv_name}",
        on_update=mocker.ANY,
        blocking=True,
        initial_value=0,
        ZNAM="Idle",
        ONAM="Active",
    )
    with pytest.raises(AssertionError):
        long_command_pv_name = long_command_name.title().replace("_", "")
        util_builder.aOut.assert_called_once_with(
            f"{DEVICE}:{long_command_pv_name}",
            initial_value=0,
            always_update=True,
            on_update=mocker.ANY,
        )


def test_non_1d_waveforms_discarded(mocker: MockerFixture):
    api = ControllerAPI(
        path=[DEVICE],
        attributes={
            "waveform_0d": AttrR(Array1D[np.int32], shape=()),
            "waveform_1d": AttrR(Array1D[np.int32], shape=(10,)),
            "waveform_2d": AttrR(Array1D[np.int32], shape=(10, 2)),
            "waveform_3d": AttrR(Array1D[np.int32], shape=(10, 2, 3)),
        },
    )

    create_mock = mocker.patch(
        "fastcs.transports.epics.ca.ioc._create_and_link_read_pv"
    )
    EpicsCAIOC([api], {})

    create_mock.assert_called_once_with(
        DEVICE, "Waveform1d", "waveform_1d", None, api.attributes["waveform_1d"]
    )


def test_update_meta(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")

    pv_name = f"{DEVICE}:Attr"

    attr_r = AttrR(int)
    record_r = _make_in_record(pv_name, attr_r)

    builder.longIn.assert_called_once_with(
        pv_name,
        LOPR=None,
        HOPR=None,
        EGU=None,
        DESC=None,
        initial_value=0,
    )
    record_r.set_field.assert_not_called()
    attr_r.update_meta(Meta(units="m", limits=NumericLimits(display=Limits(low=-3))))
    record_r.set_field.assert_any_call("EGU", "m")
    record_r.set_field.assert_any_call("LOPR", -3)

    with pytest.raises(
        TypeError,
        match="'precision' is not valid metadata for int",
    ):
        attr_r.update_meta(Meta(precision=3))

    attr_w = AttrW(int)
    record_w = _make_out_record(pv_name, attr_w, on_update=mocker.ANY)

    builder.longOut.assert_called_once_with(
        pv_name,
        DESC=None,
        LOPR=None,
        HOPR=None,
        EGU=None,
        initial_value=0,
        DRVL=None,
        DRVH=None,
        on_update=mocker.ANY,
        always_update=True,
        blocking=True,
    )
    record_w.set_field.assert_not_called()
    attr_w.update_meta(
        Meta(
            units="m",
            limits=NumericLimits(display=Limits(low=-1), control=Limits(low=-3)),
        )
    )
    record_w.set_field.assert_any_call("EGU", "m")
    record_w.set_field.assert_any_call("LOPR", -1)
    record_w.set_field.assert_any_call("DRVL", -3)

    with pytest.raises(
        TypeError,
        match="'precision' is not valid metadata for int",
    ):
        attr_w.update_meta(Meta(precision=3))


def test_ca_context_contains_softioc_commands(mocker: MockerFixture):
    transport = EpicsCATransport(mocker.MagicMock())

    softioc_commands = {
        command: getattr(softioc, command) for command in softioc.command_names
    }
    # We exclude "exit" from the context
    softioc_commands.pop("exit")

    assert transport.context == softioc_commands
