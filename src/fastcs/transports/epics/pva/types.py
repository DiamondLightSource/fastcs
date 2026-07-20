import math
import time

import numpy as np
from numpy.typing import DTypeLike
from p4p import Value
from p4p.nt import NTEnum, NTNDArray, NTScalar, NTTable

from fastcs.attributes import Attribute, AttrR, AttrW
from fastcs.datatypes import Bool, DType, Enum, Float, Int, String, Table, Waveform
from fastcs.datatypes.datatype import DType_T

P4P_ALLOWED_DATATYPES = (Int, Float, String, Bool, Enum, Waveform, Table)

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


def make_p4p_type(
    attribute: Attribute,
) -> NTScalar | NTEnum | NTNDArray | NTTable:
    """Creates a p4p type for a given `Attribute` `DataType`."""

    display = isinstance(attribute, AttrR)
    control = isinstance(attribute, AttrW)
    match attribute.datatype:
        case Int():
            return NTScalar.buildType("i", display=display, control=control)
        case Float():
            return NTScalar.buildType("d", display=display, control=control, form=True)
        case String():
            return NTScalar.buildType("s", display=display, control=control)
        case Bool():
            return NTScalar.buildType("?", display=display, control=control)
        case Enum():
            return NTEnum()
        case Waveform():
            # TODO: https://github.com/DiamondLightSource/FastCS/issues/123
            # * Make 1D scalar array for 1D shapes.
            #     This will require converting from np.int32 to "ai"
            #     if len(shape) == 1:
            #         return NTScalarArray(convert np.datatype32 to string "ad")
            # * Add an option for allowing shape to change, if so we will
            #   use an NDArray here even if shape is 1D

            return NTNDArray()
        case Table(structured_dtype):
            # TODO: `NTEnum/NTNDArray/NTTable.wrap` don't accept extra fields until
            # https://github.com/epics-base/p4p/issues/166
            return NTTable(
                columns=_table_with_numpy_dtypes_to_p4p_dtypes(structured_dtype)
            )
        case _:
            raise RuntimeError(f"DataType `{attribute.datatype}` unsupported in P4P.")


def cast_from_p4p_value(attribute: Attribute[DType_T], value: object) -> DType_T:
    """Converts from a p4p value to a FastCS `Attribute` value."""
    match attribute.datatype:
        case Enum():
            assert hasattr(value, "index"), "Got non-enum p4p.Value for Enum DataType"
            index: int = value.index  # pyright: ignore[reportAttributeAccessIssue]
            return attribute.datatype.validate(attribute.datatype.members[index])
        case Waveform(shape=shape):
            # p4p sends a flattened array
            assert value.shape == (math.prod(shape),)
            return attribute.datatype.validate(value.reshape(attribute.datatype.shape))
        case Table(structured_dtype):
            assert isinstance(value, np.ndarray)
            return attribute.datatype.validate(np.array(value, dtype=structured_dtype))
        case attribute.datatype if issubclass(
            type(attribute.datatype), P4P_ALLOWED_DATATYPES
        ):
            return attribute.datatype.validate(value)  # type: ignore
        case _:
            raise ValueError(f"Unsupported datatype {attribute.datatype}")


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
    if attribute.description is not None:
        display["description"] = attribute.description
    if isinstance(attribute.datatype, (Float | Int)):
        if attribute.datatype.max is not None:
            display["limitHigh"] = attribute.datatype.max
        if attribute.datatype.min is not None:
            display["limitLow"] = attribute.datatype.min
        if attribute.datatype.units is not None:
            display["units"] = attribute.datatype.units
    if isinstance(attribute.datatype, Float):
        if attribute.datatype.prec is not None:
            display["precision"] = attribute.datatype.prec
    if display:
        return {"display": display}
    return {}


def _p4p_check_numeric_for_alarm_states(datatype: Int | Float, value: DType) -> dict:
    low = None if datatype.min_alarm is None else value < datatype.min_alarm  # type: ignore
    high = None if datatype.max_alarm is None else value > datatype.max_alarm  # type: ignore
    severity = (
        MAJOR_ALARM_SEVERITY
        if high not in (None, False) or low not in (None, False)
        else NO_ALARM_SEVERITY
    )
    status, message = NO_ALARM_SEVERITY, "No alarm"
    if low:
        status, message = (
            RECORD_ALARM_STATUS,
            f"Below minimum alarm limit: {datatype.min_alarm}",
        )
    if high:
        status, message = (
            RECORD_ALARM_STATUS,
            f"Above maximum alarm limit: {datatype.max_alarm}",
        )

    return p4p_alarm_states(severity, status, message)


def cast_to_p4p_value(attribute: Attribute[DType_T], value: DType_T) -> object:
    """Converts a FastCS ``Attribute`` value to a p4p value"""
    match attribute.datatype:
        case Enum():
            return {
                "index": attribute.datatype.index_of(value),
                "choices": attribute.datatype.names,
            }
        case Waveform():
            return attribute.datatype.validate(value)
        case Table():
            return attribute.datatype.validate(value)

        case datatype if issubclass(type(datatype), P4P_ALLOWED_DATATYPES):
            record_fields: dict = {"value": datatype.validate(value)}
            if isinstance(attribute, AttrR):
                record_fields.update(p4p_display(attribute))

            if isinstance(datatype, (Float | Int)):
                record_fields.update(
                    _p4p_check_numeric_for_alarm_states(
                        datatype,
                        value,
                    )
                )
            else:
                record_fields.update(p4p_alarm_states())

            record_fields.update(p4p_timestamp_now())

            return Value(make_p4p_type(attribute), record_fields)
        case _:
            raise ValueError(f"Unsupported datatype {attribute.datatype}")
