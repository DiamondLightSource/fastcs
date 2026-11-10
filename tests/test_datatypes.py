from enum import Enum, IntEnum

import numpy as np
import numpy.typing as npt
import pytest

from fastcs.datatypes import (
    Array1D,
    Limits,
    Meta,
    NumericLimits,
    Table,
    check_control_limits,
    default_value,
    is_table,
    numpy_to_python_type,
    resolve_datatype,
    validate_meta,
    validate_value,
    values_equal,
)
from fastcs.datatypes.types import _BUILTIN_DTYPES


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


def test_numeric_limits_defaults():
    assert NumericLimits(display=Limits(0.0, 10.0)).control == Limits(0.0, 10.0)
    assert NumericLimits(alarm=Limits(0, 10)).warning == Limits(0, 10)


@pytest.mark.parametrize(
    "limits, value",
    [
        (NumericLimits(control=Limits(low=1)), 0),
        (NumericLimits(control=Limits(high=-1)), 0),
        (NumericLimits(control=Limits(low=1.0)), 0.0),
        (NumericLimits(control=Limits(high=-1.0)), 0.0),
        # control defaults to the display range
        (NumericLimits(display=Limits(0.0, 10.0)), -1.0),
    ],
)
def test_check_control_limits_rejects_values_outside_the_range(limits, value):
    with pytest.raises(ValueError, match="minimum|maximum"):
        check_control_limits(Meta(limits=limits), value)


def test_validate_value_does_not_check_control_limits():
    limits = NumericLimits(control=Limits(0, 10))

    assert validate_value(int, Meta(limits=limits), 15) == 15


@pytest.mark.parametrize("bound", ["1", True, [0]])
def test_limits_reject_non_numeric_bounds(bound):
    with pytest.raises(TypeError, match="Limits must be int or float"):
        Limits(low=bound)


@pytest.mark.parametrize(
    "limits, dtype",
    [
        (Limits(), int),
        (Limits(0, 10), int),
        (Limits(high=10), int),
        (Limits(0, 10.5), float),
        (Limits(low=0.5), float),
        (NumericLimits(), int),
        (NumericLimits(control=Limits(0, 10), alarm=Limits(-5, 15)), int),
        (NumericLimits(control=Limits(0, 10), alarm=Limits(-5, 15.5)), float),
    ],
)
def test_limits_dtype(limits, dtype):
    assert limits.dtype is dtype


def test_int_limits_must_be_ints():
    with pytest.raises(TypeError, match="Limits for int attribute must be int"):
        validate_meta(int, Meta(limits=NumericLimits(alarm=Limits(0, 10.5))))


def test_int_limits_are_valid_for_a_float():
    validate_meta(float, Meta(limits=NumericLimits(control=Limits(0, 10))))


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


def test_resolve_datatype():
    for builtin in _BUILTIN_DTYPES:
        assert resolve_datatype(builtin) == (builtin, None)
    assert resolve_datatype(Colour) == (Colour, None)  # Enum case

    assert resolve_datatype(Array1D[np.int32]) == (np.ndarray, np.int32)
    # numpy's own alias for a subscripted array is the same spelling with an
    # unbounded shape, so it resolves the same way.
    assert resolve_datatype(npt.NDArray[np.int32]) == (np.ndarray, np.int32)
    assert resolve_datatype(np.ndarray) == (np.ndarray, None)
    assert resolve_datatype(Table) == (np.ndarray, None)


def test_resolve_datatype_rejects_a_non_type():
    with pytest.raises(TypeError, match="Datatype must be a type"):
        resolve_datatype("float")


def test_resolve_datatype_rejects_an_unsupported_generic():
    with pytest.raises(TypeError, match="Unsupported datatype"):
        resolve_datatype(list[int])


def test_default_value():
    assert default_value(int, Meta()) == 0
    assert default_value(float, Meta()) == 0.0
    assert default_value(bool, Meta()) is False
    assert default_value(str, Meta()) == ""
    assert np.array_equal(
        default_value(np.ndarray, Meta(array_dtype=np.int32, shape=(3,))),
        np.zeros(3, dtype=np.int32),
    )
    assert default_value(np.ndarray, _TABLE_META).size == 0


@pytest.mark.parametrize(
    "dtype, control, expected",
    [
        (int, Limits(low=1), 1),
        (float, Limits(low=-1.5), 0.0),
        (float, Limits(low=-1.5, high=1.5), 0.0),
        (float, Limits(low=-2.0, high=-1.0), -2.0),
        (float, Limits(high=-1.0), -1.0),
        (int, Limits(), 0),
        (float, Limits(), 0.0),
    ],
)
def test_numeric_default_value_uses_zero_or_available_bound(dtype, control, expected):
    meta = Meta(limits=NumericLimits(control=control))
    assert default_value(dtype, meta) == expected


def test_validate_meta_rejects_fields_the_datatype_has_no_use_for():
    with pytest.raises(TypeError, match="'precision' is not valid metadata for str"):
        validate_meta(str, Meta(precision=3), "device_id")


def test_an_array_needs_an_element_type():
    with pytest.raises(TypeError, match="needs an element type"):
        default_value(np.ndarray, Meta(shape=(3,)))


def test_is_table():
    assert is_table(Meta(structured_dtype=[("a", np.int32)]))
    assert not is_table(Meta(array_dtype=np.int32))
    assert not is_table(Meta())


@pytest.mark.parametrize(
    "value, array_dtype",
    [
        ([1, 2], np.int32),
        ([1.5], np.float32),
        ([1, 2], np.float64),
        ([True], np.int32),
        (["ab"], np.dtype("U")),
    ],
)
def test_array_accepts_same_kind_casts(value, array_dtype):
    array = validate_value(np.ndarray, Meta(array_dtype=array_dtype), value)
    assert np.issubdtype(array.dtype, array_dtype)
    assert array.tolist() == value


@pytest.mark.parametrize(
    "value, array_dtype",
    [
        ([1.7], np.int32),
        ([-1], np.uint8),
        ([1], np.bool),
        (["1"], np.int32),
    ],
)
def test_array_rejects_casts_that_change_kind(value, array_dtype):
    with pytest.raises(ValueError, match="cannot be cast to the array dtype"):
        validate_value(np.ndarray, Meta(array_dtype=array_dtype), value)
