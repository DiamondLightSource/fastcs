import enum
from enum import IntEnum

import numpy as np
import pytest

from fastcs.datatypes import (
    Bool,
    DataType,
    Enum,
    Float,
    Int,
    String,
    SubsetEnum,
    Table,
    Waveform,
)
from fastcs.datatypes._util import numpy_to_fastcs_datatype
from tests.util import DeviceGain, DeviceMode, DeviceTrigger, Gain, Mode, Trigger


def test_base_validate():
    class TestInt(DataType[int]):
        @property
        def dtype(self) -> type[int]:
            return int

    class MyIntEnum(IntEnum):
        A = 0
        B = 1

    test_int = TestInt()

    assert test_int.validate("0") == 0
    assert test_int.validate(MyIntEnum.B) == 1

    with pytest.raises(ValueError, match="Failed to cast"):
        test_int.validate("foo")


@pytest.mark.parametrize(
    ["datatype", "init_args", "value"],
    [
        (Int, {"min": 1}, 0),
        (Int, {"max": -1}, 0),
        (Float, {"min": 1}, 0.0),
        (Float, {"max": -1}, 0.0),
        (Enum, {"enum_cls": int}, 0),
        (Waveform, {"array_dtype": "uint64", "shape": (1, 1)}, np.ndarray([1])),
    ],
)
def test_validate(datatype, init_args, value):
    with pytest.raises(ValueError):
        datatype(**init_args).validate(value)


@pytest.mark.parametrize(
    "datatype, expected",
    [
        (Int(min=1), 1),
        (Float(min=-1.5), 0.0),
        (Float(min=-1.5, max=1.5), 0.0),
        (Float(min=-2.0, max=-1.0), -2.0),
        (Float(max=-1.0), -1.0),
        (Int(), 0),
        (Float(), 0.0),
    ],
)
def test_numeric_initial_value_uses_zero_or_available_bound(datatype, expected):
    assert datatype.initial_value == expected


@pytest.mark.parametrize(
    "numpy_type, fastcs_datatype",
    [
        (np.float16, Float()),
        (np.float32, Float()),
        (np.int16, Int()),
        (np.int32, Int()),
        (np.bool, Bool()),
        (np.dtype("S1000"), String()),
        (np.dtype("U25"), String()),
        (np.dtype(">i4"), Int()),
        (np.dtype("d"), Float()),
    ],
)
def test_numpy_to_fastcs_datatype(numpy_type, fastcs_datatype):
    assert fastcs_datatype == numpy_to_fastcs_datatype(numpy_type)


@pytest.mark.parametrize(
    "fastcs_datatype, value1, value2, expected",
    [
        (Int(), 1, 1, True),
        (Int(), 1, 2, False),
        (Float(), 1.0, 1.0, True),
        (Float(), 1.0, 2.0, False),
        (Bool(), True, True, True),
        (Bool(), True, False, False),
        (String(), "foo", "foo", True),
        (String(), "foo", "bar", False),
        (Waveform(np.int16), np.array([1]), np.array([1]), True),
        (Waveform(np.int16), np.array([1]), np.array([2]), False),
        (
            Table([("int", np.int16), ("bool", np.bool), ("str", np.dtype("S10"))]),
            np.array([1, True, "foo"]),
            np.array([1, True, "foo"]),
            True,
        ),
        (
            Table([("int", np.int16), ("bool", np.bool), ("str", np.dtype("S10"))]),
            np.array([1, True, "foo"]),
            np.array([2, False, "bar"]),
            False,
        ),
    ],
)
def test_dataset_equal(fastcs_datatype: DataType, value1, value2, expected):
    assert fastcs_datatype.equal(value1, value2) is expected


@pytest.mark.parametrize(
    "fastcs_datatype, values, expected",
    [
        (Int(), [1, 1], True),
        (Int(), [1, 2], False),
        (Float(), [1.0, 1.0], True),
        (Float(), [1.0, 2.0], False),
        (Bool(), [True, True], True),
        (Bool(), [True, False], False),
    ],
)
def test_dataset_all_equal(fastcs_datatype: DataType, values, expected):
    assert fastcs_datatype.all_equal(values) is expected


def test_string_length():
    assert String(length=10).validate("12345678901") == "1234567890"

    assert String().validate("12345678901") == "12345678901"

    with pytest.raises(ValueError):
        String(length=0)


@pytest.mark.parametrize(
    "hinted_enum, introspected_enum, allowed_members, names",
    [
        pytest.param(Mode, None, [Mode.OFF, Mode.ON], ["OFF", "ON"], id="strict"),
        pytest.param(
            Mode,
            DeviceMode,
            [Mode.OFF, Mode.ON],
            ["OFF", "ON"],
            id="strict-with-allowed",
        ),
        pytest.param(
            Gain, DeviceGain, [Gain.LOW, Gain.HIGH], ["LOW", "HIGH"], id="superset"
        ),
        pytest.param(
            Trigger,
            DeviceTrigger,
            [Trigger.INTERNAL, Trigger.EXTERNAL, "Software"],
            ["INTERNAL", "EXTERNAL", "SOFTWARE"],
            id="subset",
        ),
    ],
)
def test_enum_types_correctly_allow_options_based_on_introspected_info(
    hinted_enum, introspected_enum, allowed_members, names
):
    test_enum = Enum(hinted_enum, allowed=introspected_enum)
    assert test_enum.members == allowed_members
    # == alone can't tell str-mixin members from raw strings, so
    # Check that members are of expected type
    assert [type(m) for m in test_enum.members] == [type(m) for m in allowed_members]
    # Check identical names
    assert test_enum.names == names


def test_enum_validate_allowed():
    subset_enum = Enum(Trigger, allowed=DeviceTrigger)
    superset_enum = Enum(Gain, allowed=DeviceGain)
    with pytest.raises(ValueError):
        superset_enum.validate(Gain.AUTO)  # hint-only member, not on device
    with pytest.raises(ValueError):
        subset_enum.validate("Typo")  # raw value not on device

    assert superset_enum.validate(DeviceGain.LOW) is Gain.LOW
    assert superset_enum.validate(DeviceGain.HIGH) is Gain.HIGH

    assert subset_enum.validate(DeviceTrigger.EXTERNAL) is Trigger.EXTERNAL
    assert subset_enum.validate(DeviceTrigger.INTERNAL) is Trigger.INTERNAL
    assert subset_enum.validate(DeviceTrigger.SOFTWARE) == "Software"


def test_subset_enum_requires_value_type():
    with pytest.raises(TypeError, match="needs a value type"):

        class NoMixin(SubsetEnum):
            A = "a"


def test_subset_enum_call():
    # Known values are members, unknown values of the mixin type are kept raw
    class IntSubsetEnum(int, SubsetEnum):
        ONE = 1
        TWO = 2

    class IntDetectorEnum(enum.IntEnum):
        ONE = 1
        TWO = 2
        THREE = 3

    assert IntSubsetEnum(1) is IntSubsetEnum.ONE
    assert IntSubsetEnum(IntDetectorEnum.TWO) is IntSubsetEnum.TWO
    raw = IntSubsetEnum(IntDetectorEnum.THREE)
    assert raw == 3
    assert not isinstance(raw, IntSubsetEnum)
    assert isinstance(raw, IntDetectorEnum)
    raw = IntSubsetEnum(4)
    assert raw == 4
    assert not isinstance(raw, (IntSubsetEnum, IntDetectorEnum))

    # Values of another type are still rejected
    with pytest.raises(ValueError):
        IntSubsetEnum("Five")


def test_subset_enum_functional_api():
    # Creating an enum with names must not be mistaken for a raw value lookup
    dynamic = SubsetEnum("Dynamic", {"A": "a"}, type=str)
    assert isinstance(dynamic, type)
    assert issubclass(dynamic, SubsetEnum)
    assert dynamic("a") is dynamic.A
