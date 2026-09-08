"""Decorator sugar over the getter/setter constructors (ADR 0018).

``AttrR.declare``/``AttrRW.declare`` are the one-decorated-getter spelling a
PyTango user expects, written over the same machinery as the procedural
``AttrR(getter=...)`` / ``AttrRW(getter=..., setter=...)`` form rather than
beside it::

    class PowerSupply(Controller):
        @AttrRW.declare(Polled(period=0.5), units="V")
        async def voltage(self) -> float:
            \"\"\"Output voltage.\"\"\"
            return float(await self._conn.query("V?"))

        @voltage.setter
        async def set_voltage(self, value: float) -> None:
            await self._conn.send(f"V={value}")

The decorator names the class it builds, so what a reader - and a type checker
- sees at the declaration is what the attribute is: ``AttrR.declare`` takes a
getter and nothing else, ``AttrRW.declare`` takes a getter and expects a
``@x.setter`` to go with it. A write-only ``AttrW`` is rare enough to write
longhand.

The setter carries a name of its own, as PyTango's ``write_voltage`` does
rather than ``@property``'s second ``def voltage``, so neither half of a
read-write attribute redeclares a name the other has already taken.

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
from typing import Any, Generic, cast, overload

from fastcs.attributes._infer_datatype import (
    _datatype_for_annotation,
    _unwrap_update_annotation,
)
from fastcs.attributes.attr_r import (
    AttrR,
    NotPolled,
    Polled,
    Schedule,
    UnboundGetter,
)
from fastcs.attributes.attr_rw import AttrRW
from fastcs.attributes.attr_w import UnboundSetter
from fastcs.attributes.update import Update
from fastcs.datatypes import DType_T, Meta
from fastcs.util import Controller_T


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


def _method_signature(fn: Callable) -> Signature:
    """Resolve the signature of an async declared getter or setter.

    Args:
        fn: The decorated function

    Returns:
        The signature, with its annotations resolved

    Raises:
        TypeError: If the function is not an async method

    """
    if not iscoroutinefunction(fn):
        raise TypeError("must be an async function")

    return signature(fn, eval_str=True)


class UnboundAttr(Generic[Controller_T, DType_T]):
    """An ``AttrR.declare``-decorated getter, and the metadata that goes with it.

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
        name: str | None = None,
    ) -> None:
        try:
            getter_signature = _method_signature(getter)
            getter_parameters = list(getter_signature.parameters.values())
            if len(getter_parameters) != 1 or any(
                parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
                for parameter in getter_parameters
            ):
                raise TypeError("must be a method taking self")
        except TypeError as error:
            raise TypeError(f"Declared getter {getter.__qualname__} {error}") from error

        annotation = _unwrap_update_annotation(getter_signature.return_annotation)
        datatype = _datatype_for_annotation(annotation)
        if datatype is None:
            if annotation is not Signature.empty:
                raise TypeError(
                    f"Declared getter {getter.__qualname__} must annotate a supported "
                    f"datatype, got {_type_name(annotation)}"
                )
            raise TypeError(
                f"Declared getter {getter.__qualname__} must annotate the datatype "
                "the attribute holds as its return type, for example `-> float`"
            )

        if isinstance(schedule, Polled | NotPolled) and schedule.getter is not None:
            raise TypeError(
                f"The schedule given to the declaration of {getter.__qualname__} "
                "already has a getter; pass a bare Polled(period=...) or NotPolled()"
            )

        self._getter = getter
        self._setter = setter
        self._schedule = schedule
        self._datatype = datatype
        self._meta: dict[str, Any] = dict(meta or {})
        self._name = name or getter.__name__

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
            f"Attribute '{self._name}' does not exist yet. A declaration "
            "becomes an attribute when the controller is constructed, so it "
            "cannot be reached before Controller.__init__ has run."
        )

    @property
    def datatype(self) -> Any:
        """The datatype inferred from the getter's return annotation."""
        return self._datatype

    @property
    def name(self) -> str:
        """The name this declaration has in the `Controller` class body."""
        return self._name

    def has_setter(self) -> bool:
        return self._setter is not None

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
    """An ``AttrRW.declare``-decorated getter, which binds an ``AttrRW``.

    A separate class so that a read-write declaration reads as the ``AttrRW``
    it becomes, and a read-only one as the ``AttrR``. It is also what carries
    `setter`: only a declaration that said it was read-write can be given one.
    """

    @overload
    def __get__(
        self, instance: None, owner: type | None = None, /
    ) -> UnboundAttrRW[Controller_T, DType_T]: ...

    @overload
    def __get__(
        self, instance: object, owner: type | None = None, /
    ) -> AttrRW[DType_T]: ...

    def __get__(self, instance: Any, owner: type | None = None, /) -> Any:
        return super().__get__(instance, owner)

    def setter(
        self, fn: UnboundSetter[Controller_T, DType_T]
    ) -> AttrSetter[Controller_T, DType_T]:
        """Declare the writer half of this attribute.

        The setter keeps a name of its own, as PyTango's ``write_voltage`` does
        for a ``voltage`` attribute, so the two halves of one attribute are
        never two declarations of one name::

            @voltage.setter
            async def set_voltage(self, value: float) -> None:
                await self._conn.send(f"V={value}")

        Args:
            fn: The setter, taking ``self`` and the value to apply

        Returns:
            An `AttrSetter` declaration, which replaces the getter's
            declaration with one carrying this setter when the class is
            created. This one is left alone, so a subclass declaring a setter
            does not also give one to the base class it inherited the getter
            from.

        Raises:
            TypeError: If the setter is not an async method taking a value, or
                annotates a value of a different datatype to the getter's

        """
        if self._setter is not None:
            raise TypeError(
                f"Declared getter {self._getter.__qualname__} already has a setter"
            )

        try:
            setter_signature = _method_signature(fn)
            setter_parameters = list(setter_signature.parameters.values())
            if len(setter_parameters) != 2 or any(
                parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
                for parameter in setter_parameters
            ):
                raise TypeError("must be a method taking self and the value to set")
        except TypeError as error:
            raise TypeError(f"Declared setter {fn.__qualname__} {error}") from error

        value = list(setter_signature.parameters.values())[1]
        if value.annotation is not Signature.empty:
            if _datatype_for_annotation(value.annotation) is not self._datatype:
                raise TypeError(
                    f"Declared setter {fn.__qualname__} takes a "
                    f"{_type_name(value.annotation)}, but its getter returns a "
                    f"{_type_name(self._datatype)}"
                )

        return AttrSetter(self, fn)

    def declare_setter_on(
        self, owner: type, fn: UnboundSetter[Controller_T, DType_T]
    ) -> None:
        """Carry a setter into this declaration, on one `Controller` class.

        Called by an `AttrSetter` when the class it was declared in is created.
        The declaration carrying the setter replaces this one in ``owner``'s
        own namespace. The getter declaration must also be declared on
        ``owner``; an inherited getter cannot be given a setter this way.

        Args:
            owner: The `Controller` class the setter was declared in
            fn: The setter, taking ``self`` and the value to apply

        Raises:
            TypeError: If ``owner`` has no matching read-write declaration, or
                the attribute already has a setter

        """
        declared = owner.__dict__.get(self._name)
        if not isinstance(declared, UnboundAttrRW):
            raise TypeError(
                f"Cannot add setter for '{self._name}' to {owner.__name__}: "
                "the read-write declaration is not defined on that class"
            )

        if declared.has_setter():
            raise TypeError(
                f"Declared getter {self._getter.__qualname__} already has a setter"
            )

        declaration = UnboundAttrRW(
            self._getter,
            schedule=self._schedule,
            meta=cast(Meta, self._meta),
            setter=fn,
            name=self._name,
        )

        setattr(owner, self._name, declaration)

    def bind(self, controller: Controller_T) -> AttrRW[DType_T]:
        if self._setter is None:
            raise TypeError(
                f"Attribute '{self._name}' was declared with AttrRW.declare but "
                "has no setter. Add one with "
                f"`@{self._name}.setter`, or declare it read-only with "
                "AttrR.declare."
            )

        return cast(AttrRW[DType_T], super().bind(controller))


class AttrSetter(Generic[Controller_T, DType_T]):
    """The writer half of an ``AttrRW.declare``, given by ``@<getter>.setter``.

    The decorated method keeps a name of its own in the class body -
    ``set_voltage`` for a ``voltage`` attribute, the way PyTango writes
    ``write_voltage`` - rather than redeclaring the getter's name. When the
    class is created this replaces the getter's declaration with a read-write
    one, so ``voltage`` binds an ``AttrRW`` while ``set_voltage`` stays
    callable as an ordinary method of the controller.

    Replacing the declaration is done on the class that declared the setter, so
    a subclass writing ``@Base.voltage.setter`` leaves ``Base`` read-only.
    """

    def __init__(
        self,
        declaration: UnboundAttrRW[Controller_T, DType_T],
        fn: UnboundSetter[Controller_T, DType_T],
    ) -> None:
        self._declaration = declaration
        self._fn = fn
        self.__doc__ = fn.__doc__

    def __set_name__(self, owner: type, name: str) -> None:
        self._declaration.declare_setter_on(owner, self._fn)

    @overload
    def __get__(
        self, instance: None, owner: type | None = None, /
    ) -> AttrSetter[Controller_T, DType_T]: ...

    @overload
    def __get__(
        self, instance: Controller_T, owner: type | None = None, /
    ) -> Callable[[DType_T], Awaitable[None | DType_T | Update[DType_T]]]: ...

    def __get__(self, instance: Any, owner: type | None = None, /) -> Any:
        if instance is None:
            return self

        return MethodType(self._fn, instance)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self._fn.__qualname__}, "
            f"attribute={self._declaration.name!r})"
        )


def declare_attribute(
    declaration_type: type[UnboundAttr[Any, Any]],
    getter_or_schedule: Any,
    meta: dict[str, Any],
) -> Any:
    """Build what ``AttrR.declare``/``AttrRW.declare`` return.

    Both spellings are the same decorator over a different declaration class,
    and both take either a getter (used bare, ``@AttrR.declare``) or a schedule
    and metadata (``@AttrR.declare(Polled(period=0.5), units="V")``).

    Args:
        declaration_type: `UnboundAttr` for a read-only declaration,
            `UnboundAttrRW` for a read-write one
        getter_or_schedule: The getter, when the decorator is used bare;
            otherwise a `Polled` or `NotPolled` schedule, or nothing
        meta: Metadata for the attribute, checked against the datatype the
            getter returns when it is bound

    Returns:
        The declaration itself for the bare form, and the decorator that makes
        one for the parameterised form

    """
    if getter_or_schedule is not None and not isinstance(
        getter_or_schedule, Polled | NotPolled
    ):
        # Used bare, so what we have is the getter itself. There is no way to
        # pass metadata in that form, so there is none to carry over.
        return declaration_type(getter_or_schedule)

    def wrapper(getter: Any) -> Any:
        return declaration_type(
            getter, schedule=getter_or_schedule, meta=cast(Meta, meta)
        )

    return wrapper
