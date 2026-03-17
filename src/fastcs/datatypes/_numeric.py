from dataclasses import dataclass
from typing import Any, TypeVar

from fastcs.datatypes.datatype import DataType

Numeric_T = TypeVar("Numeric_T", int, float)
"""A numeric type supported by a corresponding FastCS Attribute DataType"""


@dataclass(frozen=True)
class _Numeric(DataType[Numeric_T]):
    """Base class for numeric FastCS DataType classes"""

    units: str | None = None
    """The units of the numeric value"""
    min: Numeric_T | None = None
    """The minimum allowed value - values below this will raise an exception"""
    max: Numeric_T | None = None
    """The maximum allowed value - values above this will raise an exception"""
    min_alarm: Numeric_T | None = None
    """The minimum alarm limit - values below this will be set with an alarm state"""
    max_alarm: Numeric_T | None = None
    """The maximum alarm limit - values above this will be set with an alarm state"""

    def validate(self, value: Any) -> Numeric_T:
        _value = super().validate(value)

        if self.min is not None and _value < self.min:
            raise ValueError(f"Value {_value} is less than minimum {self.min}")

        if self.max is not None and _value > self.max:
            raise ValueError(f"Value {_value} is greater than maximum {self.max}")

        return _value

    @property
    def initial_value(self) -> Numeric_T:
        return self.dtype(0)
