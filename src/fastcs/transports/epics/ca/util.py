import enum
import re
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from softioc import builder
from softioc.pythonSoftIoc import RecordWrapper

from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    DEFAULT_PRECISION,
    DType,
    DType_T,
    Meta,
    NumericLimits,
)
from fastcs.exceptions import FastCSError
from fastcs.transports.epics.util import validate_epics_pv_id

_CA_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_ca_id(controller_api: ControllerAPI) -> None:
    """Reject controller ids that wouldn't be safe in an EPICS CA PV name.

    Rejects ids with characters outside ``[A-Za-z0-9_-]`` and rejects setups
    where the longest derivable PV prefix already exceeds the 60-character
    EPICS PV name limit.
    """
    validate_epics_pv_id(controller_api, transport_label="EPICS CA id", id_re=_CA_ID_RE)


_MBB_FIELD_PREFIXES = (
    "ZR",
    "ON",
    "TW",
    "TH",
    "FR",
    "FV",
    "SX",
    "SV",
    "EI",
    "NI",
    "TE",
    "EL",
    "TV",
    "TT",
    "FT",
    "FF",
)

MBB_STATE_FIELDS = tuple(f"{p}ST" for p in _MBB_FIELD_PREFIXES)
MBB_VALUE_FIELDS = tuple(f"{p}VL" for p in _MBB_FIELD_PREFIXES)
MBB_MAX_CHOICES = len(_MBB_FIELD_PREFIXES)


DEFAULT_STRING_WAVEFORM_LENGTH = 256


def is_epics_supported(dtype: type[DType]) -> bool:
    """Whether EPICS CA can serve an attribute of this datatype."""
    return (
        dtype in (bool, int, float, str)
        or issubclass(dtype, enum.Enum)
        or issubclass(dtype, np.ndarray)
    )


def enum_names(dtype: type[enum.Enum]) -> list[str]:
    """The names of an enum's members, in declaration order."""
    return [member.name for member in dtype]


def _display_limit_fields(meta: Meta) -> dict[str, Any]:
    """The record fields for the range an attribute is displayed over."""
    limits: NumericLimits | None = meta.get("limits")
    display = limits.display if limits is not None else None

    return {
        "LOPR": display.low if display is not None else None,
        "HOPR": display.high if display is not None else None,
    }


def _control_limit_fields(meta: Meta) -> dict[str, Any]:
    """The record fields for the range an attribute may be driven to."""
    limits: NumericLimits | None = meta.get("limits")
    control = limits.control if limits is not None else None

    return {
        "DRVL": control.low if control is not None else None,
        "DRVH": control.high if control is not None else None,
    }


def _string_length(meta: Meta) -> int:
    return (meta.get("length") or DEFAULT_STRING_WAVEFORM_LENGTH) + 1


def _array_length(meta: Meta) -> int:
    return meta.get("shape", DEFAULT_ARRAY_SHAPE)[0]


def _make_in_record(pv: str, attribute: AttrR) -> RecordWrapper:
    meta = attribute.meta
    dtype = attribute.dtype
    common_fields = {
        "DESC": attribute.description,
        "initial_value": cast_to_epics_type(attribute, attribute.readback),
    }

    if dtype is bool:
        record = builder.boolIn(pv, ZNAM="False", ONAM="True", **common_fields)
    elif dtype is int:
        record = builder.longIn(
            pv,
            EGU=meta.get("units"),
            **_display_limit_fields(meta),
            **common_fields,
        )
    elif dtype is float:
        record = builder.aIn(
            pv,
            EGU=meta.get("units"),
            PREC=meta.get("precision", DEFAULT_PRECISION),
            **_display_limit_fields(meta),
            **common_fields,
        )
    elif dtype is str:
        record = builder.longStringIn(pv, length=_string_length(meta), **common_fields)
    elif issubclass(dtype, enum.Enum):
        if len(enum_names(dtype)) > MBB_MAX_CHOICES:
            record = builder.longStringIn(pv, **common_fields)
        else:
            common_fields.update(create_state_keys(dtype))
            record = builder.mbbIn(pv, **common_fields)
    elif issubclass(dtype, np.ndarray):
        record = builder.WaveformIn(pv, length=_array_length(meta), **common_fields)
    else:
        raise FastCSError(f"EPICS unsupported datatype on {attribute}: {dtype}")

    _mirror_meta_onto_record(attribute, record, _in_record_fields)
    return record


def _make_out_record(pv: str, attribute: AttrW, on_update: Callable) -> RecordWrapper:
    meta = attribute.meta
    dtype = attribute.dtype
    common_fields = {
        "DESC": attribute.description,
        "initial_value": cast_to_epics_type(
            attribute,
            attribute.readback
            if isinstance(attribute, AttrRW)
            else attribute.default_value(),
        ),
        "on_update": on_update,
        "always_update": True,
        "blocking": True,
    }

    if dtype is bool:
        record = builder.boolOut(pv, ZNAM="False", ONAM="True", **common_fields)
    elif dtype is int:
        record = builder.longOut(
            pv,
            EGU=meta.get("units"),
            **_display_limit_fields(meta),
            **_control_limit_fields(meta),
            **common_fields,
        )
    elif dtype is float:
        record = builder.aOut(
            pv,
            EGU=meta.get("units"),
            PREC=meta.get("precision", DEFAULT_PRECISION),
            **_display_limit_fields(meta),
            **_control_limit_fields(meta),
            **common_fields,
        )
    elif dtype is str:
        record = builder.longStringOut(pv, length=_string_length(meta), **common_fields)
    elif issubclass(dtype, enum.Enum):
        names = enum_names(dtype)
        if len(names) > MBB_MAX_CHOICES:

            def _verify_in_names(_, value):
                return value in names

            record = builder.longStringOut(
                pv, validate=_verify_in_names, **common_fields
            )
        else:
            common_fields.update(create_state_keys(dtype))
            record = builder.mbbOut(pv, **common_fields)
    elif issubclass(dtype, np.ndarray):
        record = builder.WaveformOut(pv, length=_array_length(meta), **common_fields)
    else:
        raise FastCSError(f"EPICS unsupported datatype on {attribute}: {dtype}")

    _mirror_meta_onto_record(attribute, record, _out_record_fields)
    return record


def _in_record_fields(meta: Meta) -> dict[str, Any]:
    return {
        "PREC": meta.get("precision"),
        "EGU": meta.get("units"),
        **_display_limit_fields(meta),
    }


def _out_record_fields(meta: Meta) -> dict[str, Any]:
    return {**_in_record_fields(meta), **_control_limit_fields(meta)}


def _mirror_meta_onto_record(
    attribute: Attribute,
    record: RecordWrapper,
    fields_from_meta: Callable[[Meta], dict[str, Any]],
) -> None:
    """Push later metadata changes - new units, say - onto the record."""

    def meta_updater(meta: Meta) -> None:
        for field, value in fields_from_meta(meta).items():
            if value is not None:
                record.set_field(field, value)

    attribute.add_update_meta_callback(meta_updater)


def create_state_keys(dtype: type[enum.Enum]) -> dict[str, str]:
    """Creates a dictionary of state field keys to names"""
    return dict(
        zip(
            MBB_STATE_FIELDS,
            enum_names(dtype),
            strict=False,
        )
    )


def cast_from_epics_type(attribute: Attribute[DType_T], value: object) -> DType_T:
    """Casts from an EPICS value to an attribute's datatype."""
    dtype = attribute.dtype

    if dtype is bool:
        if value == 0:
            return False  # pyright: ignore[reportReturnType]
        elif value == 1:
            return True  # pyright: ignore[reportReturnType]
        else:
            raise ValueError(f"Invalid bool value from EPICS record {value}")

    if issubclass(dtype, enum.Enum):
        if len(enum_names(dtype)) <= MBB_MAX_CHOICES:
            assert isinstance(value, int), "Got non-integer value for Enum"
            return attribute.validate(list(dtype)[value])
        # enum backed by string record
        assert isinstance(value, str), "Got non-string value for long Enum"
        return attribute.validate(dtype[value])

    if is_epics_supported(dtype):
        return attribute.validate(value)

    raise ValueError(f"Unsupported datatype {dtype}")


def cast_to_epics_type(attribute: Attribute[DType_T], value: DType_T) -> Any:
    """Casts from an attribute's value to an EPICS value."""
    dtype = attribute.dtype

    if issubclass(dtype, enum.Enum):
        member = cast(enum.Enum, attribute.validate(value))
        if len(enum_names(dtype)) <= MBB_MAX_CHOICES:
            return list(dtype).index(member)
        # enum backed by string record
        return member.name

    if dtype is str:
        length = attribute.meta.get("length") or DEFAULT_STRING_WAVEFORM_LENGTH
        return str(value)[:length]

    if is_epics_supported(dtype):
        return value

    raise ValueError(f"Unsupported datatype {dtype}")
