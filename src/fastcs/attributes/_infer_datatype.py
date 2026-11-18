from __future__ import annotations

import inspect
import types
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin

from fastcs.attributes.update import Update
from fastcs.datatypes import resolve_datatype


def _unwrap_update_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Update:
        args = get_args(annotation)
        return args[0] if args else annotation
    if origin in (Union, types.UnionType):
        # ``T | Update[T]`` is one datatype; any other union is ambiguous
        members = {_unwrap_update_annotation(arg) for arg in get_args(annotation)}
        if len(members) == 1:
            return members.pop()
    return annotation


def _datatype_for_annotation(annotation: Any) -> Any:
    if annotation is inspect.Signature.empty:
        return None
    try:
        resolve_datatype(annotation)
    except TypeError:
        return None
    return annotation


def infer_datatype_from_getter(getter: Callable) -> Any:
    """Infer a datatype from a getter's return annotation.

    Args:
        getter: The getter to inspect

    Returns:
        The datatype spelling, or None if the annotation is missing or unsupported

    """
    signature = inspect.signature(getter, eval_str=True)
    return _datatype_for_annotation(
        _unwrap_update_annotation(signature.return_annotation)
    )


def infer_datatype_from_setter(setter: Callable) -> Any:
    """Infer a datatype from a setter's value parameter annotation.

    Args:
        setter: The setter to inspect

    Returns:
        The datatype spelling, or None if the annotation is missing or unsupported

    """
    signature = inspect.signature(setter, eval_str=True)
    parameters = list(signature.parameters.values())
    if not parameters:
        return None
    return _datatype_for_annotation(parameters[0].annotation)
