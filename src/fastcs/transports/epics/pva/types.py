import enum
import math
import time

import numpy as np
from numpy.typing import DTypeLike
from p4p import Value
from p4p.nt import NTEnum, NTNDArray, NTScalar, NTTable

from fastcs.attributes import Attribute, AttrR, AttrW
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    DEFAULT_PRECISION,
    DType,
    DType_T,
    Meta,
    NumericLimits,
)

# https://epics-base.github.io/pvxs/nt.html#alarm-t
RECORD_ALARM_STATUS = 3
NO_ALARM_STATUS = 0
MAJOR_ALARM_SEVERITY = 2
NO_ALARM_SEVERITY = 0

# https://numpy.org/devdocs/reference/arrays.dtypes.html#arrays-dtypes
# Some numpy dtypes don't match directly with the p4p ones
_NUMPY_DTYPE_TO_P4P_DTYPE = {
    "S": "s",  # Raw bytes to unicode bytes
    "U": "s",
}


def _table_with_numpy_dtypes_to_p4p_dtypes(numpy_dtypes: list[tuple[str, DTypeLike]]):
    """
    Numpy structured datatypes can use the numpy dtype class, e.g `np.int32` or the
    character, e.g "i". P4P only accepts the character so this method is used to
    convert.

    https://epics-base.github.io/p4p/values.html#type-definitions

    It also forbids:
        The numpy dtype for float16, which isn't supported in p4p.
        String types which should be supported but currently don't function:
            https://github.com/epics-base/p4p/issues/168
    """
    p4p_dtypes = []
    for name, numpy_dtype in numpy_dtypes:
        dtype_char = np.dtype(numpy_dtype).char
        dtype_char = _NUMPY_DTYPE_TO_P4P_DTYPE.get(dtype_char, dtype_char)
        if dtype_char in ("e", "U", "S"):
            raise ValueError(f"`{np.dtype(numpy_dtype)}` is unsupported in p4p.")
        p4p_dtypes.append((name, dtype_char))
    return p4p_dtypes


def is_p4p_supported(dtype: type[DType]) -> bool:
    """Whether the PVA transport can serve an attribute of this datatype."""
    return (
        dtype in (bool, int, float, str)
        or issubclass(dtype, enum.Enum)
        or issubclass(dtype, np.ndarray)
    )


def make_p4p_type(
    attribute: Attribute,
) -> NTScalar | NTEnum | NTNDArray | NTTable:
    """Creates a p4p type for a given `Attribute` datatype."""

    display = isinstance(attribute, AttrR)
    control = isinstance(attribute, AttrW)
    dtype = attribute.dtype

    if dtype is bool:
        return NTScalar.buildType("?", display=display, control=control)
    if dtype is int:
        return NTScalar.buildType("i", display=display, control=control)
    if dtype is float:
        return NTScalar.buildType("d", display=display, control=control, form=True)
    if dtype is str:
        return NTScalar.buildType("s", display=display, control=control)
    if issubclass(dtype, enum.Enum):
        return NTEnum()
    if issubclass(dtype, np.ndarray):
        if (structured_dtype := attribute.meta.get("structured_dtype")) is not None:
            # TODO: `NTEnum/NTNDArray/NTTable.wrap` don't accept extra fields until
            # https://github.com/epics-base/p4p/issues/166
            return NTTable(
                columns=_table_with_numpy_dtypes_to_p4p_dtypes(structured_dtype)
            )

        # TODO: https://github.com/DiamondLightSource/FastCS/issues/123
        # * Make 1D scalar array for 1D shapes.
        #     This will require converting from np.int32 to "ai"
        #     if len(shape) == 1:
        #         return NTScalarArray(convert np.datatype32 to string "ad")
        # * Add an option for allowing shape to change, if so we will
        #   use an NDArray here even if shape is 1D

        return NTNDArray()

    raise RuntimeError(f"Datatype `{dtype}` unsupported in P4P.")


def cast_from_p4p_value(attribute: Attribute[DType_T], value: object) -> DType_T:
    """Converts from a p4p value to a FastCS `Attribute` value."""
    dtype = attribute.dtype

    if issubclass(dtype, enum.Enum):
        assert hasattr(value, "index"), "Got non-enum p4p.Value for Enum datatype"
        index: int = value.index  # pyright: ignore[reportAttributeAccessIssue]
        return attribute.validate(list(dtype)[index])

    if issubclass(dtype, np.ndarray):
        if (structured_dtype := attribute.meta.get("structured_dtype")) is not None:
            assert isinstance(value, np.ndarray)
            return attribute.validate(np.array(value, dtype=structured_dtype))

        shape = attribute.meta.get("shape", DEFAULT_ARRAY_SHAPE)
        # p4p sends a flattened array
        assert value.shape == (math.prod(shape),)  # pyright: ignore[reportAttributeAccessIssue]
        return attribute.validate(value.reshape(shape))  # pyright: ignore[reportAttributeAccessIssue]

    if is_p4p_supported(dtype):
        return attribute.validate(value)

    raise ValueError(f"Unsupported datatype {dtype}")


def p4p_alarm_states(
    severity: int = NO_ALARM_SEVERITY,
    status: int = NO_ALARM_STATUS,
    message: str = "",
) -> dict:
    """Returns the p4p alarm structure for a given severity, status, and message."""
    return {
        "alarm": {
            "severity": severity,
            "status": status,
            "message": message,
        },
    }


def p4p_timestamp_now() -> dict:
    """The p4p timestamp structure for the current time."""
    now = time.time()
    seconds_past_epoch = int(now)
    nanoseconds = int((now - seconds_past_epoch) * 1e9)
    return {
        "timeStamp": {
            "secondsPastEpoch": seconds_past_epoch,
            "nanoseconds": nanoseconds,
        }
    }


def p4p_display(attribute: Attribute) -> dict:
    """Gets the p4p display structure for a given attribute."""
    display = {}
    meta = attribute.meta
    if attribute.description is not None:
        display["description"] = attribute.description
    if attribute.dtype in (int, float):
        limits: NumericLimits | None = meta.get("limits")
        if limits is not None:
            if limits.control.high is not None:
                display["limitHigh"] = limits.control.high
            if limits.control.low is not None:
                display["limitLow"] = limits.control.low
        if (units := meta.get("units")) is not None:
            display["units"] = units
    if attribute.dtype is float:
        display["precision"] = meta.get("precision", DEFAULT_PRECISION)
    if display:
        return {"display": display}
    return {}


def _p4p_check_numeric_for_alarm_states(meta: Meta, value: DType) -> dict:
    limits: NumericLimits | None = meta.get("limits")
    alarm = limits.alarm if limits is not None else None
    alarm_low = alarm.low if alarm is not None else None
    alarm_high = alarm.high if alarm is not None else None

    low = None if alarm_low is None else value < alarm_low  # type: ignore
    high = None if alarm_high is None else value > alarm_high  # type: ignore
    severity = (
        MAJOR_ALARM_SEVERITY
        if high not in (None, False) or low not in (None, False)
        else NO_ALARM_SEVERITY
    )
    status, message = NO_ALARM_SEVERITY, "No alarm"
    if low:
        status, message = (
            RECORD_ALARM_STATUS,
            f"Below minimum alarm limit: {alarm_low}",
        )
    if high:
        status, message = (
            RECORD_ALARM_STATUS,
            f"Above maximum alarm limit: {alarm_high}",
        )

    return p4p_alarm_states(severity, status, message)


def cast_to_p4p_value(attribute: Attribute[DType_T], value: DType_T) -> object:
    """Converts a FastCS ``Attribute`` value to a p4p value"""
    dtype = attribute.dtype

    if issubclass(dtype, enum.Enum):
        members = list(dtype)
        return {
            "index": members.index(value),  # pyright: ignore[reportArgumentType]
            "choices": [member.name for member in members],
        }

    if issubclass(dtype, np.ndarray):
        return attribute.validate(value)

    if is_p4p_supported(dtype):
        record_fields: dict = {"value": attribute.validate(value)}
        if isinstance(attribute, AttrR):
            record_fields.update(p4p_display(attribute))

        if dtype in (int, float):
            record_fields.update(
                _p4p_check_numeric_for_alarm_states(attribute.meta, value)
            )
        else:
            record_fields.update(p4p_alarm_states())

        record_fields.update(p4p_timestamp_now())

        return Value(make_p4p_type(attribute), record_fields)

    raise ValueError(f"Unsupported datatype {dtype}")
