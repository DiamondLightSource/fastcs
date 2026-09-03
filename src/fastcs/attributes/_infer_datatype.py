from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_args, get_origin

from fastcs.attributes.update import Update
from fastcs.datatypes import resolve_datatype


def _unwrap_update_annotation(annotation: Any) -> Any:
    if get_origin(annotation) is Update:
        args = get_args(annotation)
        return args[0] if args else annotation
    return annotation


def _datatype_for_annotation(annotation: Any) -> Any | None:
    """The annotation itself, if it is a datatype an attribute can hold.

    The datatype *is* the python type, so inference is just a check that the
    annotation names one FastCS supports - including subscripted spellings
    such as ``Array1D[np.int32]``.
    """
    try:
        resolve_datatype(annotation)
    except TypeError:
        return None

    return annotation


def infer_datatype_from_getter(getter: Callable) -> Any | None:
    """Infer a datatype from a getter's return type annotation."""
    signature = inspect.signature(getter, eval_str=True)
    annotation = signature.return_annotation
    if annotation is inspect.Signature.empty:
        return None
    return _datatype_for_annotation(_unwrap_update_annotation(annotation))


def infer_datatype_from_setter(setter: Callable) -> Any | None:
    """Infer a datatype from a setter's value parameter annotation."""
    signature = inspect.signature(setter, eval_str=True)
    parameters = list(signature.parameters.values())
    if not parameters:
        return None
    annotation = parameters[0].annotation
    if annotation is inspect.Signature.empty:
        return None
    return _datatype_for_annotation(annotation)
