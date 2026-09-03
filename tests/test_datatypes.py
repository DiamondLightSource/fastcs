from enum import IntEnum

import numpy as np
import pytest

from fastcs.datatypes import Bool, DataType, Enum, Float, Int, String, Table, Waveform
from fastcs.datatypes._util import numpy_to_fastcs_datatype


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
