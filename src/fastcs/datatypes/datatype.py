import enum
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import numpy as np

DType = (
    int  # Int
    | float  # Float
    | bool  # Bool
    | str  # String
    | enum.Enum  # Enum
    | np.ndarray  # Waveform / Table
)
"""A builtin (or numpy) type supported by a corresponding FastCS Attribute DataType"""

DType_T = TypeVar("DType_T", bound=DType)
"""A TypeVar of `DType` for use in generic classes and functions"""


@dataclass(frozen=True)
class DataType(Generic[DType_T]):
    """Generic datatype mapping to a python type, with additional metadata."""

    @property
    @abstractmethod
    def dtype(self) -> type[DType_T]:  # Using property due to lack of Generic ClassVars
        """Underlying python type"""
        raise NotImplementedError()

    @property
    @abstractmethod
    def initial_value(self) -> DType_T:
        """Fallback initial value if not specified in `Attribute`"""
        raise NotImplementedError()

    def validate(self, value: Any) -> DType_T:
        """Validate a value against the datatype.

        The base implementation is to try the cast and raise a useful error if it fails.

        Child classes can implement logic before calling ``super.validate(value)`` to
        modify the value passed in and help the cast succeed or after to perform further
        validation of the coerced type.

        Args:
            value: The value to validate

        Returns:
            The validated value

        Raises:
            ValueError: If the value cannot be coerced

        """
        if isinstance(value, self.dtype):
            return value

        try:
            return self.dtype(value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Failed to cast {value} to type {self.dtype}") from e

    @staticmethod
    def equal(value1: DType_T, value2: DType_T) -> bool:
        """Compare two values for equality

        Child classes can override this if the underlying type does not implement
        ``__eq__`` or to define custom logic.

        Args:
            value1: The first value to compare
            value2: The second value to compare

        Returns:
            `True` if the values are equal

        """
        return value1 == value2

    @classmethod
    def all_equal(cls, values: Sequence[DType_T]) -> bool:
        """Compare a sequence of values for equality

        Args:
            values: Values to compare

        Returns:
            `True` if all values are equal, else `False`

        """
        return all(cls.equal(values[0], value) for value in values[1:])
