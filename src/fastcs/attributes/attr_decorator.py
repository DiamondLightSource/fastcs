"""``@attr`` decorator sugar over the getter/setter constructors (ADR 0018).

``@attr`` is the one-decorated-getter spelling a PyTango user expects, written
over the same machinery as the procedural ``AttrR(getter=...)`` /
``AttrRW(getter=..., setter=...)`` form rather than beside it. It is a
decorator only - there is no free-function ``attr()`` factory, and no
``@attr_r``/``@attr_rw``: an ``AttrR`` is a decorated getter, an ``AttrRW`` is
that plus a ``@x.setter``, and a write-only ``AttrW`` is rare enough to write
longhand.

Binding follows ``@command``/``@scan``: the class body holds an `UnboundAttr`
describing the attribute, and each controller instance gets a fresh
``AttrR``/``AttrRW`` built from it at construction time. Nothing is deepcopied
from a class-scope prototype, so two instances of a controller never share an
attribute.
"""

from __future__ import annotations

from asyncio import iscoroutinefunction
from collections.abc import Awaitable, Callable
from inspect import Parameter, Signature, getdoc, signature
from types import MethodType
from typing import Any, Generic, Unpack, cast, overload

from fastcs.attributes._infer_datatype import (
    _datatype_for_annotation,
    _unwrap_update_annotation,
)
from fastcs.attributes.attr_r import AttrR, NotPolled, Polled, Schedule
from fastcs.attributes.attr_rw import AttrRW
from fastcs.attributes.update import Update
from fastcs.datatypes import DType_T, Meta
from fastcs.util import Controller_T

UnboundGetter = Callable[[Controller_T], Awaitable[DType_T | Update[DType_T]]]
"""An ``@attr`` getter, taking the `Controller` it will be bound to as ``self``"""
UnboundSetter = Callable[
    [Controller_T, DType_T], Awaitable[None | DType_T | Update[DType_T]]
]
"""An ``@x.setter`` setter, taking the `Controller` it will be bound to as ``self``"""


def _type_name(datatype: Any) -> str:
    """A datatype as it was most likely written, to name it in an error."""
    return getattr(datatype, "__name__", None) or repr(datatype)


def _summary(docstring: str | None) -> str | None:
    """The first paragraph of a docstring, as a single line.

    A description is the one-line label a transport shows next to the value, so
    a longer docstring carries only its summary into one.
    """
    if not docstring:
        return None

    return " ".join(docstring.split("\n\n", 1)[0].split()) or None


def _method_signature(fn: Callable, expected: int, kind: str) -> Signature:
    """The signature of an ``@attr`` getter or setter, once it is known to be one.

    Args:
        fn: The decorated function
        expected: How many parameters it takes, including the ``self`` it is
            bound to - one for a getter, two for a setter
        kind: What the function is, to name it in errors

    Returns:
        The signature, with its annotations resolved

    Raises:
        TypeError: If the function is not an async method of the right arity

    """
    if not iscoroutinefunction(fn):
        raise TypeError(f"@attr {kind} {fn.__qualname__} must be an async function")

    fn_signature = signature(fn, eval_str=True)
    parameters = list(fn_signature.parameters.values())
    takes = "self" if expected == 1 else "self and the value to set"
    if len(parameters) != expected or any(
        parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
        for parameter in parameters
    ):
        raise TypeError(
            f"@attr {kind} {fn.__qualname__} must be a method taking {takes}"
        )

    return fn_signature


class UnboundAttr(Generic[Controller_T, DType_T]):
    """An ``@attr``-decorated getter, and the metadata that goes with it.

    An instance of this class lives in the `Controller` class body, in place of
    the method it decorates. It is a declaration rather than an attribute: each
    `Controller` instance binds it into an ``AttrR`` of its own during
    construction, so the getter is bound to that instance and nothing is shared
    between instances.

    It is a (non-data) descriptor only so that the attribute reads as the
    ``AttrR`` it becomes - ``self.voltage.readback`` rather than the
    declaration. Once the controller has bound it the attribute is in the
    instance dictionary, which a non-data descriptor does not intercept, so
    ``__get__`` runs only before binding.
    """

    def __init__(
        self,
        getter: UnboundGetter[Controller_T, DType_T],
        schedule: Schedule[DType_T] | None = None,
        meta: Meta | None = None,
        setter: UnboundSetter[Controller_T, DType_T] | None = None,
    ) -> None:
        getter_signature = _method_signature(getter, expected=1, kind="getter")

        datatype = _datatype_for_annotation(
            _unwrap_update_annotation(getter_signature.return_annotation)
        )
        if datatype is None:
            raise TypeError(
                f"@attr getter {getter.__qualname__} must annotate the datatype "
                "the attribute holds as its return type, for example `-> float`"
            )

        if isinstance(schedule, Polled | NotPolled) and schedule.getter is not None:
            raise TypeError(
                f"The schedule given to @attr on {getter.__qualname__} already "
                "has a getter; pass a bare Polled(period=...) or NotPolled()"
            )

        self._getter = getter
        self._setter = setter
        self._schedule = schedule
        self._datatype = datatype
        self._meta: dict[str, Any] = dict(meta or {})
        self._name = getter.__name__

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    @overload
    def __get__(
        self, instance: None, owner: type | None = None, /
    ) -> UnboundAttr[Controller_T, DType_T]: ...

    @overload
    def __get__(
        self, instance: object, owner: type | None = None, /
    ) -> AttrR[DType_T]: ...

    def __get__(self, instance: Any, owner: type | None = None, /) -> Any:
        if instance is None:
            return self

        raise AttributeError(
            f"Attribute '{self._name}' does not exist yet. An @attr declaration "
            "becomes an attribute when the controller is constructed, so it "
            "cannot be reached before Controller.__init__ has run."
        )

    @property
    def datatype(self) -> Any:
        """The datatype inferred from the getter's return annotation."""
        return self._datatype

    def has_setter(self) -> bool:
        return self._setter is not None

    def setter(
        self, fn: UnboundSetter[Controller_T, DType_T]
    ) -> UnboundAttrRW[Controller_T, DType_T]:
        """Declare the writer half, making this an ``AttrRW``.

        Mirrors ``@property``/``@x.setter``, so a read-write attribute is one
        name with two decorated methods::

            @voltage.setter
            async def voltage(self, value: float) -> None:
                await self._conn.send(f"V={value}")

        Args:
            fn: The setter, taking ``self`` and the value to apply

        Returns:
            A new `UnboundAttrRW` with the setter attached. This one is left
            alone, so a subclass declaring a setter does not also give one to
            the base class it inherited the getter from.

        Raises:
            TypeError: If the setter is not an async method taking a value, or
                annotates a value of a different datatype to the getter's

        """
        if self._setter is not None:
            raise TypeError(
                f"@attr getter {self._getter.__qualname__} already has a setter"
            )

        setter_signature = _method_signature(fn, expected=2, kind="setter")

        value = list(setter_signature.parameters.values())[1]
        if value.annotation is not Signature.empty:
            if _datatype_for_annotation(value.annotation) is not self._datatype:
                raise TypeError(
                    f"@attr setter {fn.__qualname__} takes a "
                    f"{_type_name(value.annotation)}, but its getter returns a "
                    f"{_type_name(self._datatype)}"
                )

        return UnboundAttrRW(
            self._getter,
            schedule=self._schedule,
            meta=cast(Meta, self._meta),
            setter=fn,
        )

    def bind(self, controller: Controller_T) -> AttrR[DType_T]:
        """Build the attribute this declares, for one `Controller` instance.

        Args:
            controller: The controller whose methods the getter and setter are

        Returns:
            An ``AttrR``, or an ``AttrRW`` if a setter was declared

        """
        getter = MethodType(self._getter, controller)
        scheduled = getter if self._schedule is None else self._schedule(getter)

        meta = dict(self._meta)
        if "description" not in meta:
            description = _summary(getdoc(self._getter))
            if description is not None:
                meta["description"] = description

        if self._setter is None:
            attribute = AttrR(self._datatype, getter=scheduled, **meta)
        else:
            attribute = AttrRW(
                self._datatype,
                getter=scheduled,
                setter=MethodType(self._setter, controller),
                **meta,
            )

        return cast(AttrR[DType_T], attribute)

    def __repr__(self) -> str:
        access_mode = "rw" if self._setter is not None else "r"
        return (
            f"{type(self).__name__}({self._getter.__qualname__}, "
            f"access_mode={access_mode!r}, datatype={_type_name(self._datatype)})"
        )


class UnboundAttrRW(UnboundAttr[Controller_T, DType_T]):
    """An `UnboundAttr` that has been given a setter, so it binds an ``AttrRW``.

    A separate class only so that a declaration carrying a setter reads as the
    ``AttrRW`` it becomes, and one without it as an ``AttrR``.
    """

    @overload  # pyright: ignore[reportIncompatibleMethodOverride]
    def __get__(
        self, instance: None, owner: type | None = None, /
    ) -> UnboundAttrRW[Controller_T, DType_T]: ...

    @overload
    def __get__(
        self, instance: object, owner: type | None = None, /
    ) -> AttrRW[DType_T]: ...

    def __get__(self, instance: Any, owner: type | None = None, /) -> Any:
        return super().__get__(instance, owner)

    def bind(self, controller: Controller_T) -> AttrRW[DType_T]:
        return cast(AttrRW[DType_T], super().bind(controller))


@overload
def attr(
    getter: UnboundGetter[Controller_T, DType_T], /
) -> UnboundAttr[Controller_T, DType_T]: ...


@overload
def attr(
    schedule: Schedule[Any] | None = None, /, **meta: Unpack[Meta]
) -> Callable[
    [UnboundGetter[Controller_T, DType_T]], UnboundAttr[Controller_T, DType_T]
]: ...


def attr(getter_or_schedule: Any = None, /, **meta: Any) -> Any:
    """Declare an `Attribute` from the method that reads it.

    The datatype is the getter's return annotation and the getter's docstring
    is the attribute's description, so the common "one attribute, one device
    call" case is a single decorated method::

        class PowerSupply(Controller):
            @attr(Polled(period=0.5), units="V")
            async def voltage(self) -> float:
                \"\"\"Output voltage.\"\"\"
                return float(await self._conn.query("V?"))

            @voltage.setter
            async def voltage(self, value: float) -> None:
                await self._conn.send(f"V={value}")

    The optional leading positional argument is a schedule - the same
    `Polled`/`NotPolled` objects the procedural form wraps its getter in, so
    the two spellings share one vocabulary:

    - ``@attr(units="V")`` is read once, when the controller connects, which is
      what a bare ``getter=`` means and what a bare ``@attr`` means
    - ``@attr(Polled(period=0.5))`` is read every 0.5 seconds, as
      ``AttrR(getter=Polled(g, period=0.5))`` is
    - ``@attr(NotPolled())`` is never read on a schedule, as
      ``AttrR(getter=NotPolled(g))`` is

    Args:
        getter_or_schedule: The getter, when used bare as ``@attr``; otherwise
            a `Polled` or `NotPolled` schedule, or nothing
        meta: Metadata for the attribute, checked against the datatype the
            getter returns - ``precision`` on a ``str`` attribute raises

    Returns:
        An `UnboundAttr`, which each `Controller` instance binds into an
        attribute of its own

    """
    if getter_or_schedule is not None and not isinstance(
        getter_or_schedule, Polled | NotPolled
    ):
        # Bare ``@attr``, so what we have is the getter itself. There is no way
        # to pass metadata in that form, so there is none to carry over.
        return UnboundAttr(getter_or_schedule)

    def wrapper(
        getter: UnboundGetter[Controller_T, DType_T],
    ) -> UnboundAttr[Controller_T, DType_T]:
        return UnboundAttr(getter, schedule=getter_or_schedule, meta=cast(Meta, meta))

    return wrapper
