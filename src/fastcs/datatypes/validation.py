"""Validating and comparing attribute values against a datatype and its metadata.

This is what the ``DataType`` classes used to do in ``validate``/``equal``/
``initial_value``; with the datatype reduced to a python type, the behaviour
that depended on the metadata is dispatched here instead.
"""

from __future__ import annotations

import enum
from typing import Any, cast

import numpy as np

from fastcs.datatypes.limits import NumericLimits
from fastcs.datatypes.meta import (
    DEFAULT_ARRAY_SHAPE,
    DEFAULT_PRECISION,
    Array1DMeta,
    BoolMeta,
    EnumMeta,
    FloatMeta,
    IntMeta,
    Meta,
    StrMeta,
    TableMeta,
    meta_fields,
)
from fastcs.datatypes.types import DType, DType_T

_META_FOR_DTYPE: dict[type, Any] = {
    bool: BoolMeta,
    int: IntMeta,
    float: FloatMeta,
    str: StrMeta,
}


def meta_class_for(dtype: type[DType], meta: Meta) -> Any:
    """The ``*Meta`` typed dict that applies to ``dtype``."""
    if dtype in _META_FOR_DTYPE:
        return _META_FOR_DTYPE[dtype]
    if issubclass(dtype, enum.Enum):
        return EnumMeta
    if issubclass(dtype, np.ndarray):
        return TableMeta if "structured_dtype" in meta else Array1DMeta

    raise TypeError(f"Unsupported datatype {dtype!r}")


def validate_meta(dtype: type[DType], meta: Meta, name: str = "attribute") -> None:
    """Check that every field of ``meta`` is meaningful for ``dtype``.

    The runtime counterpart of the ``Unpack[*Meta]`` overloads on the
    constructors, for metadata that arrived without a static check - from a
    ``ControllerFiller`` extras object, say.

    Args:
        dtype: The python type the attribute holds
        meta: The metadata to check
        name: The attribute's name, to name it in the error

    Raises:
        TypeError: If a field is not meaningful for the datatype

    """
    allowed = meta_fields(meta_class_for(dtype, meta))
    for field in meta:
        if field not in allowed:
            raise TypeError(
                f"'{field}' is not valid metadata for {dtype.__name__} "
                f"{name} - valid fields are {', '.join(sorted(allowed))}"
            )

    length = meta.get("length")
    if length is not None and length < 1:
        raise ValueError(f"String length must be >= 1, got {length} for {name}")


def array_dtype_of(meta: Meta, element_type: Any = None) -> Any:
    """The numpy element type of an array attribute.

    Args:
        meta: The attribute's metadata
        element_type: The element type carried by the datatype spelling, if any

    Returns:
        The numpy element type

    Raises:
        TypeError: If neither source gives one

    """
    array_dtype = meta.get("array_dtype", element_type)
    if array_dtype is None:
        raise TypeError(
            "An array attribute needs an element type - subscript the datatype "
            "as Array1D[np.int32], or pass array_dtype=np.int32"
        )

    return array_dtype


def default_value(dtype: type[DType_T], meta: Meta) -> DType_T:
    """The value an attribute holds before anything has set one."""
    if dtype is str:
        return cast(DType_T, "")
    if dtype is bool:
        return cast(DType_T, False)
    if dtype in (int, float):
        return cast(DType_T, dtype(0))
    if issubclass(dtype, enum.Enum):
        return cast(DType_T, next(iter(dtype)))
    if issubclass(dtype, np.ndarray):
        if (structured_dtype := meta.get("structured_dtype")) is not None:
            return cast(DType_T, np.array([], dtype=structured_dtype))

        return cast(
            DType_T,
            np.zeros(
                meta.get("shape", DEFAULT_ARRAY_SHAPE),
                dtype=array_dtype_of(meta),
            ),
        )

    raise TypeError(f"Unsupported datatype {dtype!r}")


def values_equal(dtype: type[DType], value1: Any, value2: Any) -> bool:
    """Whether two values of ``dtype`` are equal.

    Numpy arrays need ``array_equal`` rather than ``==``, which is elementwise.
    """
    if issubclass(dtype, np.ndarray):
        return bool(np.array_equal(value1, value2))

    return bool(value1 == value2)


def validate_value(dtype: type[DType_T], meta: Meta, value: Any) -> DType_T:
    """Coerce a value to ``dtype`` and check it against ``meta``.

    Args:
        dtype: The python type the attribute holds
        meta: The attribute's metadata
        value: The value to validate

    Returns:
        The validated value

    Raises:
        ValueError: If the value cannot be coerced, or breaks the metadata

    """
    if issubclass(dtype, np.ndarray):
        return cast(DType_T, _validate_array(meta, value))

    coerced = _coerce(dtype, value)

    if dtype is float:
        precision = meta.get("precision", DEFAULT_PRECISION)
        coerced = cast(DType_T, round(cast(float, coerced), precision))
    elif dtype is str:
        return cast(DType_T, cast(str, coerced)[: meta.get("length")])

    if dtype in (int, float):
        _check_limits(cast(int | float, coerced), meta.get("limits"))

    return coerced


def _coerce(dtype: type[DType_T], value: Any) -> DType_T:
    if isinstance(value, dtype):
        return value

    try:
        return dtype(value)  # pyright: ignore[reportCallIssue]
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to cast {value} to type {dtype}") from e


def _check_limits(value: int | float, limits: NumericLimits | None) -> None:
    if limits is None:
        return

    control = limits.control
    if control.low is not None and value < control.low:
        raise ValueError(f"Value {value} is less than minimum {control.low}")
    if control.high is not None and value > control.high:
        raise ValueError(f"Value {value} is greater than maximum {control.high}")


def _validate_array(meta: Meta, value: Any) -> np.ndarray:
    if (structured_dtype := meta.get("structured_dtype")) is not None:
        array = np.asarray(value)
        if structured_dtype != array.dtype:
            raise ValueError(
                f"Value dtype {array.dtype.descr} is not the same as the "
                f"structured dtype {structured_dtype}"
            )

        return array

    array_dtype = array_dtype_of(meta)
    array = np.asarray(value).astype(array_dtype)
    if array_dtype != array.dtype:
        raise ValueError(
            f"Value dtype {array.dtype} is not the same as the array dtype "
            f"{array_dtype}"
        )

    shape = meta.get("shape", DEFAULT_ARRAY_SHAPE)
    if len(shape) != len(array.shape) or any(
        actual > maximum for actual, maximum in zip(array.shape, shape, strict=True)
    ):
        raise ValueError(
            f"Value shape {array.shape} exceeeds the shape maximum shape {shape}"
        )

    return array
