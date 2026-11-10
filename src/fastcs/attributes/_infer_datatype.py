from __future__ import annotations

import enum
import inspect
from collections.abc import Callable
from typing import Any, get_args, get_origin

from fastcs.attributes.update import Update
from fastcs.datatypes import Bool, DataType, Enum, Float, Int, String

_DEFAULT_DATATYPES: dict[type, Callable[[], DataType]] = {
    int: Int,
    float: Float,
    bool: Bool,
    str: String,
}


def _unwrap_update_annotation(annotation: Any) -> Any:
    if get_origin(annotation) is Update:
        args = get_args(annotation)
        return args[0] if args else annotation
    return annotation


def _datatype_for_type(py_type: Any) -> DataType | None:
    if py_type in _DEFAULT_DATATYPES:
        return _DEFAULT_DATATYPES[py_type]()
    if isinstance(py_type, type) and issubclass(py_type, enum.Enum):
        return Enum(py_type)
    return None


def infer_datatype_from_getter(getter: Callable) -> DataType | None:
    """Infer a default ``DataType`` from a getter's return type annotation."""
    signature = inspect.signature(getter, eval_str=True)
    annotation = signature.return_annotation
    if annotation is inspect.Signature.empty:
        return None
    return _datatype_for_type(_unwrap_update_annotation(annotation))


def infer_datatype_from_setter(setter: Callable) -> DataType | None:
    """Infer a default ``DataType`` from a setter's value parameter annotation."""
    signature = inspect.signature(setter, eval_str=True)
    parameters = list(signature.parameters.values())
    if not parameters:
        return None
    annotation = parameters[0].annotation
    if annotation is inspect.Signature.empty:
        return None
    return _datatype_for_type(annotation)
