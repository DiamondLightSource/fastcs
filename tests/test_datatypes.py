from enum import Enum, IntEnum

import numpy as np
import pytest

from fastcs.datatypes import (
    Array1D,
    Limits,
    Meta,
    NumericLimits,
    Table,
    default_value,
    numpy_to_python_type,
    resolve_datatype,
    validate_meta,
    validate_value,
    values_equal,
)


class Colour(Enum):
    RED = "red"


_TABLE_META = Meta(
    structured_dtype=[("int", np.int16), ("bool", np.bool), ("str", np.dtype("S10"))]
)


def test_coerces_to_the_datatype():
    class MyIntEnum(IntEnum):
        A = 0
        B = 1

    assert validate_value(int, Meta(), "0") == 0
    assert validate_value(int, Meta(), MyIntEnum.B) == 1

    with pytest.raises(ValueError, match="Failed to cast"):
        validate_value(int, Meta(), "foo")


@pytest.mark.parametrize(
    ["dtype", "meta", "value"],
    [
        (int, Meta(limits=NumericLimits(control=Limits(low=1))), 0),
        (int, Meta(limits=NumericLimits(control=Limits(high=-1))), 0),
        (float, Meta(limits=NumericLimits(control=Limits(low=1))), 0.0),
        (float, Meta(limits=NumericLimits(control=Limits(high=-1))), 0.0),
        (
            np.ndarray,
            Meta(array_dtype="uint64", shape=(1, 1)),
            np.ndarray([1]),
        ),
    ],
)
def test_rejects_values_outside_the_metadata(dtype, meta, value):
    with pytest.raises(ValueError):
        validate_value(dtype, meta, value)


def test_control_limits_default_to_the_display_range():
    limits = NumericLimits(display=Limits(0.0, 10.0))

    assert limits.control == Limits(0.0, 10.0)
    with pytest.raises(ValueError, match="less than minimum"):
        validate_value(float, Meta(limits=limits), -1.0)


def test_warning_limits_default_to_the_alarm_range():
    assert NumericLimits(alarm=Limits(0, 10)).warning == Limits(0, 10)


def test_warning_limits_must_lie_within_the_alarm_range():
    with pytest.raises(ValueError, match="not within alarm limits"):
        NumericLimits(alarm=Limits(0, 10), warning=Limits(-1, 11))


@pytest.mark.parametrize(
    "numpy_type, python_type",
    [
        (np.float16, float),
        (np.float32, float),
        (np.int16, int),
        (np.int32, int),
        (np.bool, bool),
        (np.dtype("S1000"), str),
        (np.dtype("U25"), str),
        (np.dtype(">i4"), int),
        (np.dtype("d"), float),
    ],
)
def test_numpy_to_python_type(numpy_type, python_type):
    assert numpy_to_python_type(numpy_type) is python_type


@pytest.mark.parametrize(
    "dtype, value1, value2, expected",
    [
        (int, 1, 1, True),
        (int, 1, 2, False),
        (float, 1.0, 1.0, True),
        (float, 1.0, 2.0, False),
        (bool, True, True, True),
        (bool, True, False, False),
        (str, "foo", "foo", True),
        (str, "foo", "bar", False),
        (np.ndarray, np.array([1]), np.array([1]), True),
        (np.ndarray, np.array([1]), np.array([2]), False),
        (
            np.ndarray,
            np.array([1, True, "foo"]),
            np.array([1, True, "foo"]),
            True,
        ),
        (
            np.ndarray,
            np.array([1, True, "foo"]),
            np.array([2, False, "bar"]),
            False,
        ),
    ],
)
def test_values_equal(dtype, value1, value2, expected):
    assert values_equal(dtype, value1, value2) is expected


def test_string_length():
    assert validate_value(str, Meta(length=10), "12345678901") == "1234567890"
    assert validate_value(str, Meta(), "12345678901") == "12345678901"

    with pytest.raises(ValueError, match="String length must be >= 1"):
        validate_meta(str, Meta(length=0))


def test_float_is_rounded_to_its_precision():
    assert validate_value(float, Meta(precision=3), 1.23456) == 1.235
    assert validate_value(float, Meta(), 1.23456) == 1.23


@pytest.mark.parametrize(
    "spelling, dtype, element_type",
    [
        (int, int, None),
        (float, float, None),
        (bool, bool, None),
        (str, str, None),
        (Array1D[np.int32], np.ndarray, np.int32),
        (np.ndarray, np.ndarray, None),
        (Table, np.ndarray, None),
        (Colour, Colour, None),
    ],
)
def test_resolve_datatype(spelling, dtype, element_type):
    assert resolve_datatype(spelling) == (dtype, element_type)


@pytest.mark.parametrize("spelling", ["float", 3, list[int]])
def test_resolve_datatype_rejects_unsupported_spellings(spelling):
    with pytest.raises(TypeError):
        resolve_datatype(spelling)


@pytest.mark.parametrize(
    "dtype, meta, expected",
    [
        (int, Meta(), 0),
        (float, Meta(), 0.0),
        (bool, Meta(), False),
        (str, Meta(), ""),
    ],
)
def test_default_value(dtype, meta, expected):
    assert default_value(dtype, meta) == expected


def test_default_value_of_an_array():
    assert np.array_equal(
        default_value(np.ndarray, Meta(array_dtype=np.int32, shape=(3,))),
        np.zeros(3, dtype=np.int32),
    )


def test_default_value_of_a_table():
    assert default_value(np.ndarray, _TABLE_META).size == 0


def test_validate_meta_rejects_fields_the_datatype_has_no_use_for():
    with pytest.raises(TypeError, match="'precision' is not valid metadata for str"):
        validate_meta(str, Meta(precision=3), "device_id")


def test_an_array_needs_an_element_type():
    with pytest.raises(TypeError, match="needs an element type"):
        default_value(np.ndarray, Meta(shape=(3,)))
