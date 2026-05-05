import enum

import pytest

from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Bool, Enum, Float, Int, String
from fastcs.transports.epics.ca.util import (
    cast_from_epics_type,
    cast_to_epics_type,
    validate_ca_id,
)


class ShortEnum(enum.Enum):
    NOT = 0
    TOO = 1
    MANY = 2
    VALUES = 3


class LongEnum(enum.Enum):
    THIS = 0
    IS = 1
    AN = 2
    ENUM = 3
    WITH = 4
    ALTOGETHER = 5
    TOO = 6
    MANY = 7
    VALUES = 8
    TO = 9
    BE = 10
    DESCRIBED = 11
    BY = 12
    MBB = 14
    TYPE = 15
    EPICS = 16
    RECORDS = 17


class LongMixedEnum(enum.Enum):
    THIS = "the value is THIS"
    IS = 1
    AN = "the value is AN"
    ENUM = 3
    WITH = "the value is WITH"
    ALTOGETHER = 5
    TOO = "the value is TOO"
    MANY = 7
    VALUES = "the value is VALUES"
    TO = 9
    BE = "the value is BE"
    DESCRIBED = 11
    BY = "the value is BY"
    MBB = 13
    TYPE = "the value is TYPE"
    EPICS = None
    RECORDS = "the value is RECORDS"


class ShortMixedEnum(enum.Enum):
    STRING_MEMBER = "I am a string"
    INT_MEMBER = 2
    NONE_MEMBER = None


@pytest.mark.parametrize(
    "datatype,input,output",
    [
        (Enum(ShortEnum), ShortEnum.TOO, 1),
        # in CA, enums with too many values become epics strings
        (Enum(LongMixedEnum), LongMixedEnum.BE, "BE"),  # string value
        (Enum(LongMixedEnum), LongMixedEnum.EPICS, "EPICS"),  # None value
        (Enum(LongMixedEnum), LongMixedEnum.MBB, "MBB"),  # int value
        (Int(), 4, 4),
        (Float(), 1.0, 1.0),
        (Bool(), True, True),
        (String(), "a" * 257, "a" * 256),
        (String(length=3), "1234", "123"),
        # shorter enums can be represented by integers from 0-15
        (Enum(ShortMixedEnum), ShortMixedEnum.STRING_MEMBER, 0),
        (Enum(ShortMixedEnum), ShortMixedEnum.INT_MEMBER, 1),
        (Enum(ShortMixedEnum), ShortMixedEnum.NONE_MEMBER, 2),
    ],
)
def test_casting_to_epics(datatype, input, output):
    assert cast_to_epics_type(datatype, input) == output


@pytest.mark.parametrize(
    "datatype, input",
    [
        # TODO cover Waveform and Table cases
        (Enum(ShortEnum), LongEnum.TOO),  # wrong enum.Enum class
    ],
)
def test_cast_to_epics_validations(datatype, input):
    with pytest.raises(ValueError):
        cast_to_epics_type(datatype, input)


@pytest.mark.parametrize(
    "datatype,from_epics,result",
    [
        # long enums backed by strings
        (Enum(LongMixedEnum), "BE", LongMixedEnum.BE),  # string value
        (Enum(LongMixedEnum), "EPICS", LongMixedEnum.EPICS),  # None value
        (Enum(LongMixedEnum), "MBB", LongMixedEnum.MBB),  # int value
        (Int(), 4, 4),
        (Float(), 1.0, 1.0),
        (Bool(), True, True),
        (String(), "hey", "hey"),
        (Enum(ShortEnum), 2, ShortEnum.MANY),
        # short enums backed by mbbi/mbbo
        (Enum(ShortMixedEnum), 0, ShortMixedEnum.STRING_MEMBER),
        (Enum(ShortMixedEnum), 1, ShortMixedEnum.INT_MEMBER),
        (Enum(ShortMixedEnum), 2, ShortMixedEnum.NONE_MEMBER),
        (Bool(), 1, True),
        (Bool(), 0, False),
    ],
)
def test_cast_from_epics_type(datatype, from_epics, result):
    assert cast_from_epics_type(datatype, from_epics) == result


@pytest.mark.parametrize(
    "datatype, input",
    [
        (object(), 0),
        (Bool(), 3),
    ],
)
def test_cast_from_epics_validations(datatype, input):
    with pytest.raises(ValueError):
        cast_from_epics_type(datatype, input)


@pytest.mark.parametrize("id", ["DEVICE", "my-id", "name_1", "ABC-123_xyz"])
def test_validate_ca_id_accepts_valid(id):
    validate_ca_id(ControllerAPI(path=[id]))


@pytest.mark.parametrize("id", ["bad/id", "with space", "colons:in:id", ""])
def test_validate_ca_id_rejects_illegal_characters(id):
    with pytest.raises(ValueError, match="EPICS CA id"):
        validate_ca_id(ControllerAPI(path=[id]))


def test_validate_ca_id_rejects_overlong_prefix():
    deep_path = ["A" * 50, "deeper_sub_controller_path"]
    with pytest.raises(ValueError, match="exceeds the EPICS"):
        validate_ca_id(
            ControllerAPI(
                path=deep_path[:1],
                sub_apis={"sub": ControllerAPI(path=deep_path)},
            )
        )
