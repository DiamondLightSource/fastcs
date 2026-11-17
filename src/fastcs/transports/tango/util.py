import re
from dataclasses import asdict
from typing import Any

from tango import AttrDataFormat

from fastcs.attributes import Attribute
from fastcs.datatypes import (
    Bool,
    DataType,
    DType,
    DType_T,
    Enum,
    Float,
    Int,
    String,
    Waveform,
)

TANGO_ALLOWED_DATATYPES = (Bool, DataType, Enum, Float, Int, String, Waveform)

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


DATATYPE_FIELD_TO_SERVER_FIELD = {
    "units": "unit",
    "min": "min_value",
    "max": "max_value",
    "min_alarm": "min_alarm",
    "max_alarm": "min_alarm",
}


def get_server_metadata_from_attribute(
    attribute: Attribute[DType],
) -> dict[str, Any]:
    """Gets the metadata for a Tango field from an attribute."""
    arguments = {}
    arguments["doc"] = attribute.description if attribute.description else ""
    return arguments


def get_server_metadata_from_datatype(datatype: DataType[DType]) -> dict[str, str]:
    """Gets the metadata for a Tango field from a FastCS datatype."""
    arguments = {
        DATATYPE_FIELD_TO_SERVER_FIELD[field]: value
        for field, value in asdict(datatype).items()
        if field in DATATYPE_FIELD_TO_SERVER_FIELD
    }

    dtype = datatype.dtype

    match datatype:
        case Waveform():
            dtype = datatype.array_dtype
            match len(datatype.shape):
                case 1:
                    arguments["max_dim_x"] = datatype.shape[0]
                    arguments["dformat"] = AttrDataFormat.SPECTRUM
                case 2:
                    arguments["max_dim_x"], arguments["max_dim_y"] = datatype.shape
                    arguments["dformat"] = AttrDataFormat.IMAGE
                case _:
                    raise TypeError(
                        f"Unsupported shape {datatype.shape}, Tango supports up "
                        "to 2D arrays"
                    )
        case Float():
            arguments["format"] = f"%.{datatype.prec}"

    arguments["dtype"] = dtype
    for argument, value in arguments.items():
        if value is None:
            arguments[argument] = ""

    return arguments


def cast_to_tango_type(datatype: DataType[DType_T], value: DType_T) -> object:
    """Casts a value from FastCS to tango datatype."""
    match datatype:
        case Enum():
            return datatype.index_of(datatype.validate(value))
        case datatype if issubclass(type(datatype), TANGO_ALLOWED_DATATYPES):
            return datatype.validate(value)
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")


def cast_from_tango_type(datatype: DataType[DType_T], value: object) -> DType_T:
    """Casts a value from tango to FastCS datatype."""
    match datatype:
        case Enum():
            assert isinstance(value, int), "Got non-integer value for Enum"
            return datatype.validate(datatype.members[value])
        case datatype if issubclass(type(datatype), TANGO_ALLOWED_DATATYPES):
            return datatype.validate(value)  # type: ignore
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")
