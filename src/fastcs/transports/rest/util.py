import re

import numpy as np

from fastcs.datatypes import Bool, DataType, DType_T, Enum, Float, Int, String, Waveform

REST_ALLOWED_DATATYPES = (Bool, DataType, Enum, Float, Int, String)

_REST_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_rest_id(id: str) -> None:
    """Reject controller ids that wouldn't be safe in a REST URL path."""
    if not id:
        raise ValueError("Controller id is empty; ids must be non-empty")
    if not _REST_ID_RE.fullmatch(id):
        raise ValueError(
            f"Controller id {id!r} is not a valid REST id; "
            "only alphanumerics, '-' and '_' are allowed"
        )


def convert_datatype(datatype: DataType[DType_T]) -> type[DType_T]:
    """Converts a datatype to a rest serialisable type."""
    match datatype:
        case Waveform():
            return list
        case _:
            return datatype.dtype


def cast_to_rest_type(datatype: DataType[DType_T], value: DType_T) -> object:
    """Casts from an attribute value to a rest value."""
    match datatype:
        case Waveform():
            return value.tolist()
        case datatype if issubclass(type(datatype), REST_ALLOWED_DATATYPES):
            return datatype.validate(value)
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")


def cast_from_rest_type(datatype: DataType[DType_T], value: object) -> DType_T:
    """Casts from a rest value to an attribute datatype."""
    match datatype:
        case Waveform():
            return datatype.validate(np.array(value, dtype=datatype.array_dtype))
        case datatype if issubclass(type(datatype), REST_ALLOWED_DATATYPES):
            return datatype.validate(value)  # type: ignore
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")
