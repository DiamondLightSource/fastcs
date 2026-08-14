"""Per-datatype metadata, as typed dicts (ADR 0014).

Each python type an `Attribute` can hold has a ``*Meta`` typed dict saying what
metadata is meaningful for it - ``precision`` for a ``float``, ``length`` for a
``str``, ``shape`` for an array. The ``Attr*`` constructors unpack the right one
for the datatype they were given, so ``AttrRW(str, precision=3)`` is a static
type error rather than a field silently ignored at runtime.

`Meta` is the superset of every field, all optional. It is what a declarative
extras object (such as the demo's ``SCPIParam``) takes, since an
``Annotated[...]`` extra cannot tie its metadata to the attribute's datatype
statically - the `ControllerFiller` validates those at fill time instead.
"""

from __future__ import annotations

from typing import Any, TypedDict

from numpy.typing import DTypeLike

from fastcs.datatypes.limits import NumericLimits

DEFAULT_PRECISION = 2
"""Decimal places a ``float`` attribute is rounded to when unspecified"""

DEFAULT_ARRAY_SHAPE: tuple[int, ...] = (2000,)
"""Maximum shape of an array attribute when unspecified"""


class CommonMeta(TypedDict, total=False):
    """Metadata meaningful for an attribute of any datatype."""

    description: str
    """Human readable description of what the attribute is"""
    group: str
    """Name of the group to display the attribute under"""


class BoolMeta(CommonMeta, total=False):
    """Metadata for a ``bool`` attribute."""


class IntMeta(CommonMeta, total=False):
    """Metadata for an ``int`` attribute."""

    units: str
    """The units of the value"""
    limits: NumericLimits[int]
    """The control, display, alarm and warning ranges of the value"""


class FloatMeta(CommonMeta, total=False):
    """Metadata for a ``float`` attribute."""

    units: str
    """The units of the value"""
    limits: NumericLimits[float]
    """The control, display, alarm and warning ranges of the value"""
    precision: int
    """Number of decimal places to round to and display"""


class StrMeta(CommonMeta, total=False):
    """Metadata for a ``str`` attribute."""

    length: int
    """Maximum length of the string. Must be >= 1"""


class EnumMeta(CommonMeta, total=False):
    """Metadata for an `enum.Enum` attribute.

    Display only - the choices come from the enum class itself.
    """


class Array1DMeta(CommonMeta, total=False):
    """Metadata for an `Array1D` attribute."""

    array_dtype: DTypeLike
    """Numpy element type, if not given by the datatype subscript"""
    shape: tuple[int, ...]
    """Maximum shape of the array"""


class TableMeta(CommonMeta, total=False):
    """Metadata for a `Table` attribute."""

    structured_dtype: list[tuple[str, DTypeLike]]
    """The columns of the table, as a numpy structured dtype"""


class Meta(CommonMeta, total=False):
    """Every metadata field, all optional.

    The spelling for metadata that cannot be tied to a datatype statically -
    a declarative extras object collecting whatever the protocol layer was
    told, validated against the datatype when the attribute is built.

    Spelled out rather than inheriting every ``*Meta``, because ``IntMeta`` and
    ``FloatMeta`` disagree on the type of ``limits``.
    """

    units: str
    limits: NumericLimits[int] | NumericLimits[float]
    precision: int
    length: int
    array_dtype: DTypeLike
    shape: tuple[int, ...]
    structured_dtype: list[tuple[str, DTypeLike]]


def meta_fields(meta_cls: Any) -> frozenset[str]:
    """The field names of a ``*Meta`` typed dict, including inherited ones."""
    return frozenset(meta_cls.__optional_keys__) | frozenset(meta_cls.__required_keys__)
