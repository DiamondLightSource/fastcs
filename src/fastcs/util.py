import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from fastcs.controllers import BaseController  # noqa: F401

Controller_T = TypeVar("Controller_T", bound="BaseController")  # noqa: F821
"""Generic `Controller` class that an unbound method must be called with as `self`"""

ONCE = float("inf")
"""Sentinel value to call a ``scan`` or io ``update`` method once on start up"""


def snake_to_pascal(name: str) -> str:
    """Converts string from snake case to Pascal case.
    If string is not a valid snake case it will be returned unchanged
    """
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name):
        name = re.sub(r"(?:^|_)([a-z0-9])", lambda match: match.group(1).upper(), name)
    return name


@contextmanager
def optional_dependency(*names: str) -> Iterator[None]:
    """Skip the imports in the block if one of the given packages isn't installed.

    Example::

        with optional_dependency("httpx"):
            from .http_connection import HTTPConnection as HTTPConnection

    Args:
        names: The packages that the block may be missing

    Raises:
        ModuleNotFoundError: If a module other than the given packages is missing

    """
    try:
        yield
    except ModuleNotFoundError as e:
        if e.name is None or e.name.split(".")[0] not in names:
            raise
