import re

import numpy as np

from fastcs.attributes import Attribute
from fastcs.datatypes import DType, DType_T, array_dtype_of

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


def convert_datatype(dtype: type[DType]) -> type:
    """Converts a datatype to a rest serialisable type."""
    if issubclass(dtype, np.ndarray):
        return list

    return dtype


def cast_to_rest_type(attribute: Attribute[DType_T], value: DType_T) -> object:
    """Casts from an attribute value to a rest value."""
    if issubclass(attribute.dtype, np.ndarray):
        return value.tolist()  # pyright: ignore[reportAttributeAccessIssue]

    return attribute.validate(value)


def cast_from_rest_type(attribute: Attribute[DType_T], value: object) -> DType_T:
    """Casts from a rest value to an attribute datatype."""
    if issubclass(attribute.dtype, np.ndarray):
        return attribute.validate(np.array(value, dtype=array_dtype_of(attribute.meta)))

    return attribute.validate(value)
