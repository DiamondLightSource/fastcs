import enum
import re
from typing import Any

import numpy as np
import pytest
from pytest_mock import MockerFixture
from softioc import alarm, softioc
from tests.assertable_controller import (
    AssertableControllerAPI,
    MyTestAttributeIORef,
    MyTestController,
)
from tests.util import ColourEnum

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import Bool, Enum, Float, Int, String, Waveform
from fastcs.exceptions import FastCSError
from fastcs.methods import Command
from fastcs.transports.epics.ca import EpicsCATransport
from fastcs.transports.epics.ca.ioc import (
    EpicsCAIOC,
    _add_alias,
    _add_attr_pvi_info,
    _add_command_enum_alias,
    _add_pvi_info,
    _add_read_enum_alias,
    _add_sub_controller_pvi_info,
    _add_write_enum_alias,
    _create_and_link_command_pv,
    _create_and_link_read_pv,
    _create_and_link_write_pv,
    _get_read_enum_attr_from_type,
    _get_write_enum_attr_from_type,
    _resolve_mapping,
    _reverse_mapping,
)
from fastcs.transports.epics.ca.util import (
    _make_in_record,
    _make_out_record,
)
from fastcs.transports.epics.options import EnumMapping
from fastcs.transports.epics.util import EPICS_MAX_NAME_LENGTH

DEVICE = "DEVICE"

SEVENTEEN_VALUES = [str(i) for i in range(1, 18)]


class OnOffStates(enum.IntEnum):
    DISABLED = 0
    ENABLED = 1


class GapEnum(enum.IntEnum):
    LOW = 1
    HIGH = 5


class PlainEnum(enum.Enum):
    LOW = "low"
    HIGH = "high"


async def do_nothing(): ...


@pytest.mark.asyncio
async def test_create_and_link_read_pv(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")
    add_attr_pvi_info = mocker.patch(
        "fastcs.transports.epics.ca.ioc._add_attr_pvi_info"
    )
    record = make_record.return_value

    attribute = AttrR(Int())
    attribute.add_on_update_callback = mocker.MagicMock()

    _create_and_link_read_pv("PREFIX", "PV", "attr", None, attribute)

    make_record.assert_called_once_with("PREFIX:PV", attribute)
    add_attr_pvi_info.assert_called_once_with(record, "PREFIX", "attr", "r")

    # Extract the callback generated and set in the function and call it
    attribute.add_on_update_callback.assert_called_once_with(mocker.ANY)
    record_set_callback = attribute.add_on_update_callback.call_args[0][0]
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
    _add_alias(record, alias_name, "attr")
    record.add_alias.assert_called_once_with(alias_name)

    _add_alias(record, too_long_alias_name, "attr")

    with pytest.raises(AssertionError):
        # assert alias that is too long is not added
        record.add_alias.assert_called_once_with(too_long_alias_name)


@pytest.mark.parametrize("alias_type", ("read", "write", "command"))
@pytest.mark.asyncio
async def test_enum_alias_skips_pv_if_too_long(mocker: MockerFixture, alias_type: str):
    alias = EnumMapping(pv="alias", mapping={"One": 1})
    mocker.patch(
        "fastcs.transports.epics.ca.ioc.EPICS_MAX_NAME_LENGTH", len(alias.pv) - 1
    )
    make_in_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")

    if alias_type == "read":
        _add_read_enum_alias(alias, AttrR(Int()), AttrR(Enum(OnOffStates)))
    elif alias_type == "write":
        _add_write_enum_alias(alias, AttrW(Int()), AttrW(Enum(OnOffStates)))
    else:
        _add_command_enum_alias(alias, Command(do_nothing), AttrW(Enum(OnOffStates)))

    make_in_record.assert_not_called()
    make_out_record.assert_not_called()


@pytest.mark.parametrize(
    "datatype,mapping,expected",
    [
        (Enum(GapEnum), {"Off": 1, "On": 5}, {"Off": GapEnum.LOW, "On": GapEnum.HIGH}),
        (
            Enum(PlainEnum),
            {"Off": "low", "On": "high"},
            {"Off": PlainEnum.LOW, "On": PlainEnum.HIGH},
        ),
        (Int(), {"Off": 0, "On": 10}, {"Off": 0, "On": 10}),
    ],
)
def test_resolve_mapping_converts_to_attribute_datatype(datatype, mapping, expected):
    resolved = _resolve_mapping(EnumMapping(pv="A", mapping=mapping), datatype)

    assert resolved is not None
    assert resolved == expected
    assert all(type(value) is type(expected[key]) for key, value in resolved.items())


# EnumMapping does not validate at runtime, so untyped config can pass a list
unhashable_mapping: Any = {"a": [1, 2]}


@pytest.mark.parametrize(
    "datatype,mapping,message",
    [
        # 7 has no member in GapEnum
        (Enum(GapEnum), {"On": 7}, "mapping is invalid"),
        # many-to-one mapping
        (Int(), {"a": 1, "b": 1}, "multiple names to the same value"),
        (Waveform(np.int32, shape=(2,)), unhashable_mapping, "mapping is invalid"),
    ],
)
def test_resolve_mapping_warns_and_returns_none_on_invalid_mapping(
    datatype, mapping, message, loguru_caplog
):
    alias = EnumMapping(pv="A", mapping=mapping)

    assert _resolve_mapping(alias, datatype) is None
    assert message in loguru_caplog.text


@pytest.mark.parametrize("alias_type", ("read", "write"))
def test_enum_alias_skips_pv_if_mapping_invalid(mocker: MockerFixture, alias_type):
    make_in_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    alias = EnumMapping(pv="A", mapping={"On": 7})

    if alias_type == "read":
        attribute = AttrR(Enum(GapEnum))
        _add_read_enum_alias(alias, attribute, _get_read_enum_attr_from_type(alias))
    else:
        attribute = AttrRW(Enum(GapEnum))
        _add_write_enum_alias(alias, attribute, _get_write_enum_attr_from_type(alias))

    make_in_record.assert_not_called()
    make_out_record.assert_not_called()


def test_reverse_mapping():
    assert _reverse_mapping({"Off": GapEnum.LOW, "On": GapEnum.HIGH}) == {
        GapEnum.LOW: "Off",
        GapEnum.HIGH: "On",
    }


@pytest.mark.parametrize("alias_index,expected", [(0, GapEnum.LOW), (1, GapEnum.HIGH)])
@pytest.mark.asyncio
async def test_write_enum_alias_puts_mapped_value(
    mocker: MockerFixture, alias_index: int, expected: GapEnum
):
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    alias = EnumMapping(pv="A", mapping={"Off": 1, "On": 5})
    attribute = AttrRW(Enum(GapEnum))

    _add_write_enum_alias(alias, attribute, _get_write_enum_attr_from_type(alias))
    await make_out_record.call_args.kwargs["on_update"](alias_index)

    assert attribute.get() == expected


@pytest.mark.asyncio
async def test_write_enum_alias_syncs_from_attribute(mocker: MockerFixture):
    record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")()
    alias = EnumMapping(pv="A", mapping={"Off": 1, "On": 5})
    attribute = AttrRW(Enum(GapEnum))

    _add_write_enum_alias(alias, attribute, _get_write_enum_attr_from_type(alias))
    await attribute.update(GapEnum.HIGH)

    record.set.assert_called_with(1, process=False)


@pytest.mark.asyncio
async def test_write_enum_alias_warns_on_unmapped_value(
    mocker: MockerFixture, loguru_caplog
):
    record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")()
    alias = EnumMapping(pv="A", mapping={"Off": 1, "On": 5})
    attribute = AttrRW(Int())

    _add_write_enum_alias(alias, attribute, _get_write_enum_attr_from_type(alias))
    record.set.reset_mock()
    await attribute.update(3)

    record.set.assert_not_called()
    assert "Ignoring enum setpoint sync" in loguru_caplog.text


@pytest.mark.asyncio
async def test_read_enum_alias_sets_record_from_plain_enum(mocker: MockerFixture):
    record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")()
    alias = EnumMapping(pv="A", mapping={"Off": "low", "On": "high"})
    attribute = AttrR(Enum(PlainEnum))

    _add_read_enum_alias(alias, attribute, _get_read_enum_attr_from_type(alias))
    await attribute.update(PlainEnum.HIGH)

    record.set.assert_called_with(1)


def test_read_enum_alias_sets_record_from_initial_value(mocker: MockerFixture):
    record = mocker.patch("fastcs.transports.epics.ca.ioc._make_in_record")()
    alias = EnumMapping(pv="A", mapping={"Off": 1, "On": 5})
    attribute = AttrR(Enum(GapEnum), initial_value=GapEnum.HIGH)

    _add_read_enum_alias(alias, attribute, _get_read_enum_attr_from_type(alias))

    record.set.assert_called_once_with(1)


@pytest.mark.parametrize("alias_index,called", [(0, False), (1, True)])
@pytest.mark.asyncio
async def test_command_enum_alias_converts_mapping_to_bool(
    mocker: MockerFixture, alias_index: int, called: bool
):
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    calls = []

    async def fn():
        calls.append(True)

    alias = EnumMapping(pv="A", mapping={"Idle": 0, "Go": 1})

    _add_command_enum_alias(alias, Command(fn), _get_write_enum_attr_from_type(alias))
    await make_out_record.call_args.kwargs["on_update"](alias_index)

    assert bool(calls) is called


@pytest.mark.asyncio
async def test_ioc_raises_if_duplicate_aliases_provided(mocker: MockerFixture):
    aliases = {"A": "Alias", "B": "Alias"}
    with pytest.raises(
        RuntimeError, match=re.escape("duplicate aliases were provided: ['Alias']")
    ):
        EpicsCAIOC(mocker.MagicMock(), aliases)


@pytest.mark.parametrize(
    ("create_pv", "add_helper", "expected_type", "mock_attribute"),
    [
        (
            _create_and_link_write_pv,
            "_add_write_enum_alias",
            AttrW,
            AttrRW(Int()),
        ),
        (
            _create_and_link_command_pv,
            "_add_command_enum_alias",
            AttrW,
            Command(do_nothing),
        ),
        (
            _create_and_link_read_pv,
            "_add_read_enum_alias",
            AttrR,
            AttrR(Int()),
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_and_link_pv_adds_enum_mapping(
    mocker: MockerFixture,
    create_pv,
    add_helper: str,
    expected_type: type[AttrW] | type[AttrR],
    mock_attribute: AttrRW | AttrR | Command,
):
    add_enum_alias = mocker.patch(f"fastcs.transports.epics.ca.ioc.{add_helper}")
    enum_mapping = EnumMapping(pv="enum_alias", mapping={"One": 1, "Two": 2})

    create_pv(
        "PREFIX",
        "PV",
        "attr",
        enum_mapping,
        mock_attribute,
    )

    add_enum_alias.assert_called_once()
    alias, passed_attribute, enum_attr = add_enum_alias.call_args.args

    assert alias == enum_mapping
    assert passed_attribute == mock_attribute
    assert isinstance(enum_attr, expected_type)
    assert isinstance(enum_attr.datatype, Enum)
    assert enum_attr.datatype.names == ["One", "Two"]


@pytest.mark.parametrize(
    "attribute,record_type,kwargs",
    (
        (
            AttrR(String()),
            "longStringIn",
            {"length": 257, "DESC": None, "initial_value": ""},
        ),
        (
            AttrR(String(length=10)),
            "longStringIn",
            {"length": 11, "DESC": None, "initial_value": ""},
        ),
        (
            AttrR(Enum(ColourEnum)),
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
                Enum(
                    enum.IntEnum(
                        "ONOFF_STATES",
                        {"DISABLED": 0, "ENABLED": 1},
                    )
                )
            ),
            "mbbIn",
            {"ZRST": "DISABLED", "ONST": "ENABLED", "DESC": None, "initial_value": 0},
        ),
        (
            AttrR(Waveform(np.int32, (10,))),
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


def test_make_record_raises(mocker: MockerFixture):
    mocker.patch("fastcs.transports.epics.ca.util.cast_to_epics_type")
    # Pass a mock as attribute to provoke the fallback case matching on datatype
    with pytest.raises(FastCSError):
        _make_in_record("PV", mocker.MagicMock())


@pytest.mark.asyncio
async def test_create_and_link_write_pv(mocker: MockerFixture):
    make_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    add_attr_pvi_info = mocker.patch(
        "fastcs.transports.epics.ca.ioc._add_attr_pvi_info"
    )
    record = make_record.return_value

    attribute = AttrW(Int())
    attribute.put = mocker.AsyncMock()
    attribute.add_sync_setpoint_callback = mocker.MagicMock()

    _create_and_link_write_pv("PREFIX", "PV", "attr", None, attribute)

    make_record.assert_called_once_with("PREFIX:PV", attribute, on_update=mocker.ANY)
    add_attr_pvi_info.assert_called_once_with(record, "PREFIX", "attr", "w")

    # Extract the write update callback generated and set in the function and call it
    attribute.add_sync_setpoint_callback.assert_called_once_with(mocker.ANY)
    sync_setpoint_callback = attribute.add_sync_setpoint_callback.call_args[0][0]
    await sync_setpoint_callback(1)

    record.set.assert_called_once_with(1, process=False)

    # Extract the on update callback generated and set in the function and call it
    on_update_callback = make_record.call_args[1]["on_update"]
    await on_update_callback(1)

    attribute.put.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_write_pv_invalid_enum_index_put_sets_alarm(
    mocker: MockerFixture, loguru_caplog
):
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    set_alarm = mocker.patch("fastcs.transports.epics.ca.ioc._set_alarm")
    attribute = AttrW(Enum(GapEnum))
    attribute.put = mocker.AsyncMock()

    _create_and_link_write_pv("PREFIX", "PV", "attr", None, attribute)
    # GapEnum only has 2 members
    await make_out_record.call_args.kwargs["on_update"](4)

    attribute.put.assert_not_called()
    set_alarm.assert_called_once_with(make_out_record.return_value, alarm.MAJOR_ALARM)
    assert "Ignoring put 4" in loguru_caplog.text


@pytest.mark.parametrize("alias_type", ("write", "command"))
@pytest.mark.asyncio
async def test_enum_alias_invalid_index_put_sets_alarm(
    mocker: MockerFixture, alias_type: str, loguru_caplog
):
    make_out_record = mocker.patch("fastcs.transports.epics.ca.ioc._make_out_record")
    set_alarm = mocker.patch("fastcs.transports.epics.ca.ioc._set_alarm")
    record = make_out_record.return_value

    alias = EnumMapping(pv="A", mapping={"Off": 1, "On": 5})
    attribute = AttrW(Enum(GapEnum))
    attribute.put = mocker.AsyncMock()
    calls = []

    async def fn():
        calls.append(True)

    enum_attr = _get_write_enum_attr_from_type(alias)
    if alias_type == "write":
        _add_write_enum_alias(alias, attribute, enum_attr)
    else:
        _add_command_enum_alias(alias, Command(fn), enum_attr)

    # The alias enum only has 2 members
    await make_out_record.call_args.kwargs["on_update"](4)

    attribute.put.assert_not_called()
    assert not calls
    set_alarm.assert_called_once_with(record, alarm.MAJOR_ALARM)
    assert "Ignoring put 4" in loguru_caplog.text


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
            AttrW(Enum(enum.IntEnum("ONOFF_STATES", {"DISABLED": 0, "ENABLED": 1}))),
            "mbbOut",
            {
                "ZRST": "DISABLED",
                "ONST": "ENABLED",
                "DESC": None,
                "initial_value": 0,
            },
        ),
        (
            AttrW(String()),
            "longStringOut",
            {"length": 257, "DESC": None, "initial_value": ""},
        ),
        (
            AttrW(String(length=10)),
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
    attribute = AttrRW(Enum(LongEnum))
    pv = "PV"
    record = _make_out_record(pv, attribute, on_update=update)
    validator = builder.longStringOut.call_args.kwargs["validate"]
    assert validator(record, "THIS")  # value is one of the Enum names
    assert not validator(record, "an invalid string value")


def test_long_enum_in_creation(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    attribute = AttrR(Enum(LongEnum))
    pv = "PV"
    _make_in_record(pv, attribute)
    assert builder.longStringIn.call_args.kwargs["initial_value"] == "THIS"


def test_get_output_record_raises(mocker: MockerFixture):
    mocker.patch("fastcs.transports.epics.ca.util.cast_to_epics_type")
    # Pass a mock as attribute to provoke the fallback case matching on datatype
    with pytest.raises(FastCSError):
        _make_out_record("PV", mocker.MagicMock(), on_update=mocker.MagicMock())


class EpicsController(MyTestController):
    read_int = AttrR(Int(), io_ref=MyTestAttributeIORef())
    read_write_int = AttrRW(Int(), io_ref=MyTestAttributeIORef())
    read_write_float = AttrRW(Float())
    read_bool = AttrR(Bool())
    write_bool = AttrW(Bool(), io_ref=MyTestAttributeIORef())
    read_string = AttrRW(String())
    enum = AttrRW(Enum(enum.IntEnum("Enum", {"RED": 0, "GREEN": 1, "BLUE": 2})))
    one_d_waveform = AttrRW(Waveform(np.int32, (10,)))


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


class ControllerLongNames(Controller):
    attr_r_with_reallyreallyreallyreallyreallyreallyreally_long_name = AttrR(Int())
    attr_rw_with_a_reallyreally_long_name_that_is_too_long_for_rbv = AttrRW(Int())
    attr_rw_short_name = AttrRW(Int())
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
            "waveform_0d": AttrR(Waveform(np.int32, shape=())),
            "waveform_1d": AttrR(Waveform(np.int32, shape=(10,))),
            "waveform_2d": AttrR(Waveform(np.int32, shape=(10, 2))),
            "waveform_3d": AttrR(Waveform(np.int32, shape=(10, 2, 3))),
        },
    )

    create_mock = mocker.patch(
        "fastcs.transports.epics.ca.ioc._create_and_link_read_pv"
    )
    EpicsCAIOC([api], {})

    create_mock.assert_called_once_with(
        DEVICE, "Waveform1d", "waveform_1d", None, api.attributes["waveform_1d"]
    )


def test_update_datatype(mocker: MockerFixture):
    builder = mocker.patch("fastcs.transports.epics.ca.util.builder")

    pv_name = f"{DEVICE}:Attr"

    attr_r = AttrR(Int())
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
    attr_r.update_datatype(Int(units="m", min_alarm=-3))
    record_r.set_field.assert_any_call("EGU", "m")
    record_r.set_field.assert_any_call("LOPR", -3)

    with pytest.raises(
        ValueError,
        match="Attribute datatype must be of type <class 'fastcs.datatypes.int.Int'>",
    ):
        attr_r.update_datatype(String())  # type: ignore

    attr_w = AttrW(Int())
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
    attr_w.update_datatype(Int(units="m", min_alarm=-1, min=-3))
    record_w.set_field.assert_any_call("EGU", "m")
    record_w.set_field.assert_any_call("LOPR", -1)
    record_w.set_field.assert_any_call("DRVL", -3)

    with pytest.raises(
        ValueError,
        match="Attribute datatype must be of type <class 'fastcs.datatypes.int.Int'>",
    ):
        attr_w.update_datatype(String())  # type: ignore


def test_ca_context_contains_softioc_commands(mocker: MockerFixture):
    transport = EpicsCATransport(mocker.MagicMock())

    softioc_commands = {
        command: getattr(softioc, command) for command in softioc.command_names
    }
    # We exclude "exit" from the context
    softioc_commands.pop("exit")

    assert transport.context == softioc_commands
