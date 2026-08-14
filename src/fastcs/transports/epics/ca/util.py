import enum
import re
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from softioc import builder
from softioc.pythonSoftIoc import RecordWrapper

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Bool, DataType, DType_T, Enum, Float, Int, String, Waveform
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


EPICS_ALLOWED_DATATYPES = (Bool, Enum, Float, Int, String, Waveform)
DEFAULT_STRING_WAVEFORM_LENGTH = 256

DATATYPE_FIELD_TO_IN_RECORD_FIELD = {
    "prec": "PREC",
    "units": "EGU",
    "min_alarm": "LOPR",
    "max_alarm": "HOPR",
}

DATATYPE_FIELD_TO_OUT_RECORD_FIELD = {
    "prec": "PREC",
    "units": "EGU",
    "min": "DRVL",
    "max": "DRVH",
    "min_alarm": "LOPR",
    "max_alarm": "HOPR",
}


def _make_in_record(pv: str, attribute: AttrR) -> RecordWrapper:
    common_fields = {
        "DESC": attribute.description,
        "initial_value": cast_to_epics_type(attribute.datatype, attribute.readback),
    }

    match attribute.datatype:
        case Bool():
            record = builder.boolIn(pv, ZNAM="False", ONAM="True", **common_fields)
        case Int():
            record = builder.longIn(
                pv,
                LOPR=attribute.datatype.min_alarm,
                HOPR=attribute.datatype.max_alarm,
                EGU=attribute.datatype.units,
                **common_fields,
            )
        case Float():
            record = builder.aIn(
                pv,
                LOPR=attribute.datatype.min_alarm,
                HOPR=attribute.datatype.max_alarm,
                EGU=attribute.datatype.units,
                PREC=attribute.datatype.prec,
                **common_fields,
            )
        case String():
            record = builder.longStringIn(
                pv,
                length=(attribute.datatype.length + 1)
                if attribute.datatype.length
                else DEFAULT_STRING_WAVEFORM_LENGTH + 1,
                **common_fields,
            )
        case Enum():
            if len(attribute.datatype.members) > MBB_MAX_CHOICES:
                record = builder.longStringIn(
                    pv,
                    **common_fields,
                )
            else:
                common_fields.update(create_state_keys(attribute.datatype))
                record = builder.mbbIn(
                    pv,
                    **common_fields,
                )
        case Waveform():
            record = builder.WaveformIn(
                pv, length=attribute.datatype.shape[0], **common_fields
            )
        case _:
            raise FastCSError(
                f"EPICS unsupported datatype on {attribute}: {attribute.datatype}"
            )

    def datatype_updater(datatype: DataType):
        for name, value in asdict(datatype).items():
            if name in DATATYPE_FIELD_TO_IN_RECORD_FIELD:
                record.set_field(DATATYPE_FIELD_TO_IN_RECORD_FIELD[name], value)

    attribute.add_update_datatype_callback(datatype_updater)
    return record


def _make_out_record(pv: str, attribute: AttrW, on_update: Callable) -> RecordWrapper:
    common_fields = {
        "DESC": attribute.description,
        "initial_value": cast_to_epics_type(
            attribute.datatype,
            attribute.readback
            if isinstance(attribute, AttrRW)
            else attribute.datatype.initial_value,
        ),
        "on_update": on_update,
        "always_update": True,
        "blocking": True,
    }

    match attribute.datatype:
        case Bool():
            record = builder.boolOut(pv, ZNAM="False", ONAM="True", **common_fields)
        case Int():
            record = builder.longOut(
                pv,
                LOPR=attribute.datatype.min_alarm,
                HOPR=attribute.datatype.max_alarm,
                EGU=attribute.datatype.units,
                DRVL=attribute.datatype.min,
                DRVH=attribute.datatype.max,
                **common_fields,
            )
        case Float():
            record = builder.aOut(
                pv,
                LOPR=attribute.datatype.min_alarm,
                HOPR=attribute.datatype.max_alarm,
                EGU=attribute.datatype.units,
                PREC=attribute.datatype.prec,
                DRVL=attribute.datatype.min,
                DRVH=attribute.datatype.max,
                **common_fields,
            )
        case String():
            record = builder.longStringOut(
                pv,
                length=(attribute.datatype.length + 1)
                if attribute.datatype.length
                else DEFAULT_STRING_WAVEFORM_LENGTH + 1,
                **common_fields,
            )
        case Enum():
            if len(attribute.datatype.members) > MBB_MAX_CHOICES:
                datatype: Enum = attribute.datatype

                def _verify_in_datatype(_, value):
                    return value in datatype.names

                record = builder.longStringOut(
                    pv,
                    validate=_verify_in_datatype,
                    **common_fields,
                )

            else:
                common_fields.update(create_state_keys(attribute.datatype))
                record = builder.mbbOut(
                    pv,
                    **common_fields,
                )
        case Waveform():
            record = builder.WaveformOut(
                pv,
                length=attribute.datatype.shape[0],
                **common_fields,
            )
        case _:
            raise FastCSError(
                f"EPICS unsupported datatype on {attribute}: {attribute.datatype}"
            )

    def datatype_updater(datatype: DataType):
        for name, value in asdict(datatype).items():
            if name in DATATYPE_FIELD_TO_OUT_RECORD_FIELD:
                record.set_field(DATATYPE_FIELD_TO_OUT_RECORD_FIELD[name], value)

    attribute.add_update_datatype_callback(datatype_updater)
    return record


def create_state_keys(datatype: Enum):
    """Creates a dictionary of state field keys to names"""
    return dict(
        zip(
            MBB_STATE_FIELDS,
            datatype.names,
            strict=False,
        )
    )


def cast_from_epics_type(datatype: DataType[DType_T], value: object) -> DType_T:
    """Casts from an EPICS datatype to a FastCS datatype."""
    match datatype:
        case Bool():
            if value == 0:
                return False
            elif value == 1:
                return True
            else:
                raise ValueError(f"Invalid bool value from EPICS record {value}")
        case Enum():
            if len(datatype.members) <= MBB_MAX_CHOICES:
                assert isinstance(value, int), "Got non-integer value for Enum"
                return datatype.validate(datatype.members[value])
            else:  # enum backed by string record
                assert isinstance(value, str), "Got non-string value for long Enum"
                # python typing can't narrow the nested generic enum_cls
                assert issubclass(datatype.enum_cls, enum.Enum), "Invalid Enum.enum_cls"
                enum_member = datatype.enum_cls[value]
                return datatype.validate(enum_member)
        case datatype if issubclass(type(datatype), EPICS_ALLOWED_DATATYPES):
            return datatype.validate(value)  # type: ignore
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")


def cast_to_epics_type(datatype: DataType[DType_T], value: DType_T) -> Any:
    """Casts from an attribute's datatype to an EPICS datatype."""
    match datatype:
        case Enum():
            if len(datatype.members) <= MBB_MAX_CHOICES:
                return datatype.index_of(datatype.validate(value))
            else:  # enum backed by string record
                return datatype.validate(value).name
        case String() as string:
            if string.length is not None:
                return value[: string.length]
            else:
                return value[:DEFAULT_STRING_WAVEFORM_LENGTH]
        case datatype if issubclass(type(datatype), EPICS_ALLOWED_DATATYPES):
            return value
        case _:
            raise ValueError(f"Unsupported datatype {datatype}")
