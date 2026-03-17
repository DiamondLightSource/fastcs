from dataclasses import dataclass

import numpy as np
from numpy.typing import DTypeLike

from fastcs.datatypes.datatype import DataType


@dataclass(frozen=True)
class Waveform(DataType[np.ndarray]):
    array_dtype: DTypeLike
    """Numpy array dtype"""
    shape: tuple[int, ...] = (2000,)
    """Numpy array shape"""

    @property
    def dtype(self) -> type[np.ndarray]:
        return np.ndarray

    @property
    def initial_value(self) -> np.ndarray:
        return np.zeros(self.shape, dtype=self.array_dtype)

    def validate(self, value: np.ndarray) -> np.ndarray:
        _value = super().validate(np.asarray(value).astype(self.array_dtype))

        if self.array_dtype != _value.dtype:
            raise ValueError(
                f"Value dtype {_value.dtype} is not the same as the array dtype "
                f"{self.array_dtype}"
            )

        if len(self.shape) != len(_value.shape) or any(
            shape1 > shape2
            for shape1, shape2 in zip(_value.shape, self.shape, strict=True)
        ):
            raise ValueError(
                f"Value shape {_value.shape} exceeeds the shape maximum shape "
                f"{self.shape}"
            )

        return _value

    @staticmethod
    def equal(value1: np.ndarray, value2: np.ndarray) -> bool:
        return np.array_equal(value1, value2)
