import enum
import re
from typing import Any, cast

import numpy as np
from tango import AttrDataFormat

from fastcs.attributes import Attribute
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    DEFAULT_PRECISION,
    DType,
    DType_T,
    NumericLimits,
    array_dtype_of,
)

_TANGO_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_tango_id(id: str) -> None:
    """Reject controller ids that wouldn't be safe in a Tango device-name segment."""
    if not id:
        raise ValueError("Controller id is empty; ids must be non-empty")
    if not _TANGO_ID_RE.fullmatch(id):
        raise ValueError(
            f"Controller id {id!r} is not a valid Tango id; "
            "only alphanumerics, '-' and '_' are allowed"
        )


def tango_dev_class_name(id: str) -> str:
    """Map a controller id to a valid Python class name for a Tango device class.

    Hyphens are replaced with underscores; a leading digit is prefixed with ``X``.
    Assumes ``id`` has already been accepted by ``validate_tango_id``.
    """
    sanitized = id.replace("-", "_")
    if sanitized[0].isdigit():
        sanitized = "X" + sanitized
    return sanitized


def tango_dev_name(id: str, dsr_instance: str) -> str:
    """Build the three-segment Tango device name for a controller.

    The id forms the leading segment, followed by the per-id Tango device class
    and the DSR instance name. Assumes ``id`` has been accepted by
    ``validate_tango_id``.
    """
    return f"{id}/{tango_dev_class_name(id)}/{dsr_instance}"


def get_server_metadata_from_attribute(
    attribute: Attribute[DType],
) -> dict[str, Any]:
    """Gets the metadata for a Tango field from an attribute."""
    arguments = {}
    arguments["doc"] = attribute.description if attribute.description else ""
    return arguments


def _limit_arguments(limits: NumericLimits | None) -> dict[str, Any]:
    if limits is None:
        return {}

    return {
        "min_value": limits.control.low,
        "max_value": limits.control.high,
        "min_alarm": limits.alarm.low,
        "max_alarm": limits.alarm.high,
        "min_warning": limits.warning.low,
        "max_warning": limits.warning.high,
    }


def get_server_metadata_from_datatype(attribute: Attribute[DType]) -> dict[str, Any]:
    """Gets the metadata for a Tango field from an attribute's datatype."""
    meta = attribute.meta
    dtype: Any = attribute.dtype

    arguments: dict[str, Any] = {"unit": meta.get("units")}
    arguments.update(_limit_arguments(meta.get("limits")))

    if issubclass(attribute.dtype, np.ndarray):
        dtype = array_dtype_of(meta)
        shape = meta.get("shape", DEFAULT_ARRAY_SHAPE)
        match len(shape):
            case 1:
                arguments["max_dim_x"] = shape[0]
                arguments["dformat"] = AttrDataFormat.SPECTRUM
            case 2:
                arguments["max_dim_x"] = shape[0]
                arguments["max_dim_y"] = shape[1]
                arguments["dformat"] = AttrDataFormat.IMAGE
            case _:
                raise TypeError(
                    f"Unsupported shape {shape}, Tango supports up to 2D arrays"
                )
    elif attribute.dtype is float:
        arguments["format"] = f"%.{meta.get('precision', DEFAULT_PRECISION)}"

    arguments["dtype"] = dtype
    for argument, value in arguments.items():
        if value is None:
            arguments[argument] = ""

    return arguments


def cast_to_tango_type(attribute: Attribute[DType_T], value: DType_T) -> object:
    """Casts a value from FastCS to tango datatype."""
    if issubclass(attribute.dtype, enum.Enum):
        member = cast(enum.Enum, attribute.validate(value))
        return list(attribute.dtype).index(member)

    return attribute.validate(value)


def cast_from_tango_type(attribute: Attribute[DType_T], value: object) -> DType_T:
    """Casts a value from tango to FastCS datatype."""
    if issubclass(attribute.dtype, enum.Enum):
        assert isinstance(value, int), "Got non-integer value for Enum"
        return attribute.validate(list(attribute.dtype)[value])

    return attribute.validate(value)
