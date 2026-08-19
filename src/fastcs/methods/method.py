from asyncio import iscoroutinefunction
from collections.abc import Callable, Coroutine
from inspect import Signature, getdoc, signature
from typing import Any, Generic

from fastcs.tracer import Tracer
from fastcs.util import Controller_T

MethodCallback = Callable[..., Coroutine[None, None, Any]]
"""Generic protocol for all `Controller` Method callbacks"""


class Method(Generic[Controller_T], Tracer):
    """Generic base class for all FastCS Controller methods."""

    def __init__(self, fn: MethodCallback, *, group: str | None = None) -> None:
        super().__init__()

        self._docstring = getdoc(fn)
        self._signature = signature(fn, eval_str=True)
        self._validate(fn)

        self._fn = fn
        self._group = group
        self.enabled = True

    def _validate(self, fn: MethodCallback) -> None:
        if not iscoroutinefunction(fn):
            raise TypeError("Method must be async function")

    def _validate_takes_no_arguments(self, expected: int) -> None:
        """Reject a method that takes anything beyond its bound ``self``.

        Args:
            expected: How many parameters a no-argument method has here - one
                for an unbound method, which still declares ``self``

        Raises:
            TypeError: If the method takes arguments. The message describes
                the fault alone - the caller catches it to say what kind of
                method it was.

        """
        if len(self.parameters) != expected:
            raise TypeError("method cannot have arguments")

    def _validate_returns_nothing(self) -> None:
        """Reject a method that declares a return type.

        Raises:
            TypeError: If the method returns something. The message describes
                the fault alone - the caller catches it to say what kind of
                method it was.

        """
        if self.return_type not in (None, Signature.empty):
            raise TypeError("method return type must be None or empty")

    @property
    def signature(self) -> Signature:
        """The signature of the wrapped function.

        This is the public description of how to call the method, and what it
        gives back - transports read it to decide how to expose the method, and
        whether they can expose it at all.
        """
        return self._signature

    @property
    def return_type(self):
        return self._signature.return_annotation

    @property
    def parameters(self):
        return self._signature.parameters

    @property
    def docstring(self):
        return self._docstring

    @property
    def fn(self):
        return self._fn

    @property
    def group(self):
        return self._group
