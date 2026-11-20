from __future__ import annotations

import functools
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


def _resolve_annotation(fn: Callable, annotation: Any) -> Any:
    """Evaluate a string annotation in the namespace ``fn`` was defined in.

    Only the one annotation the datatype comes from is evaluated, so an unrelated
    annotation that cannot be resolved - such as a return type imported only under
    ``TYPE_CHECKING`` - does not stop the datatype being inferred.
    """
    if not isinstance(annotation, str):
        return annotation
    while isinstance(fn, functools.partial):
        fn = fn.func
    fn = inspect.unwrap(fn)
    try:
        return eval(annotation, getattr(fn, "__globals__", {}))
    except NameError:
        # A string annotation naming something not in the function's globals
        return inspect.Signature.empty


def infer_datatype_from_getter(getter: Callable) -> Any:
    """Infer a datatype from a getter's return annotation.

    Args:
        getter: The getter to inspect

    Returns:
        The datatype spelling, or None if the annotation is missing, unsupported,
        or cannot be resolved

    """
    annotation = _resolve_annotation(
        getter, inspect.signature(getter).return_annotation
    )
    return _datatype_for_annotation(_unwrap_update_annotation(annotation))


def infer_datatype_from_setter(setter: Callable) -> Any:
    """Infer a datatype from a setter's value parameter annotation.

    Args:
        setter: The setter to inspect

    Returns:
        The datatype spelling, or None if the annotation is missing, unsupported
        or cannot be resolved

    """
    parameters = list(inspect.signature(setter).parameters.values())
    if not parameters:
        return None
    return _datatype_for_annotation(
        _resolve_annotation(setter, parameters[0].annotation)
    )
