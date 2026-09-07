"""Create a `Controller`'s children from the type hints in its class body.

The declarative half of ADR 0013: a class body holds *declarations*, and the
data that turns a declaration into a working attribute arrives later - from a
device's own description of itself, or from a protocol library's static
metadata. `ControllerFiller` is what stands between the two. It is a structural
port of ophyd-async's ``DeviceFiller``; the names follow FastCS's vocabulary
rather than ophyd-async's.

A hint that says what it holds is **created unfilled** while the controller is
still constructing::

    class OdinDetector(Controller):
        frames: AttrRW[int]        # exists as soon as __init__ returns

        async def initialise(self) -> None:
            self.filler.fill_attribute("frames", getter=..., setter=...)

so ``self.frames`` can be referenced by the rest of ``__init__`` - the rule
ADR 0013 takes from ophyd-async, and what makes ``initialise`` safe to run in
parallel across controllers. Filling provisions the IO and metadata on the
attribute that is already there, so a reference taken during ``__init__`` stays
valid.

A hint that cannot say what it holds - ``state: AttrR``, where the datatype is
an enum whose members only exist on the wire - is a **promise** instead: it is
not created, and `ControllerFiller.check_filled` requires that introspection
added it by the time the controller is initialised.
"""

from __future__ import annotations

import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Unpack,
    get_args,
    get_origin,
    get_type_hints,
)

from fastcs.attributes import Attribute, AttrR, AttrW
from fastcs.attributes.attr_r import Getter, Schedule
from fastcs.attributes.attr_w import Setter
from fastcs.datatypes import DType_T, Meta
from fastcs.methods import Method

if TYPE_CHECKING:
    from fastcs.controllers.base_controller import BaseController


@dataclass
class Declaration:
    """One class-body hint the filler found, and what became of it."""

    name: str
    """The attribute name on the controller, with any trailing underscore gone"""
    raw_name: str
    """The name as the class body wrote it, trailing underscore and all"""
    hint: Any
    """The declared type, with ``Annotated``/``Optional`` unwrapped"""
    declared_type: type
    """The class the hint names - ``AttrR`` for an ``AttrR[int]`` hint"""
    datatype: Any = None
    """The datatype the hint subscripts its class with, or ``None`` for a hint
    that does not say what it holds"""
    extras: tuple[Any, ...] = ()
    """Whatever else an ``Annotated`` hint carried, for a protocol layer to read"""
    optional: bool = False
    """Whether the hint was ``| None``, so `ControllerFiller.check_filled` does
    not require it"""
    child: Attribute | None = None
    """The unfilled `Attribute` this created, if it could create one"""


@dataclass
class _Hint:
    """A class-body hint, taken apart."""

    type_: Any
    """The hint with ``Annotated``/``| None`` stripped - ``AttrR[int]``"""
    declared_type: Any
    """What the hint is a subscript of - ``AttrR``, and ``AttrR`` for a bare
    ``AttrR`` too. Not necessarily a class: a ``list[int] | str`` hint leaves a
    ``typing.Union`` here, which is why the filler checks before using it."""
    datatype: Any = None
    """The subscript, where there is exactly one - ``int`` for ``AttrR[int]``"""
    extras: tuple[Any, ...] = field(default_factory=tuple)
    optional: bool = False


def _unwrap(hint: Any) -> _Hint:
    """Strip ``Annotated`` and ``| None`` off a hint, keeping what they carried."""
    extras: tuple[Any, ...] = ()
    optional = False

    # Annotated[X, ...] carries its extras on __metadata__, and X on the origin.
    metadata = getattr(hint, "__metadata__", None)
    if metadata is not None:
        extras = tuple(metadata)
        hint = hint.__origin__

    if get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        optional = len(args) != len(get_args(hint))
        if len(args) == 1:
            hint = args[0]
            # Annotated may sit inside the union rather than outside it.
            inner = _unwrap(hint)
            hint, extras = inner.type_, extras or inner.extras

    return _Hint(
        type_=hint,
        declared_type=get_origin(hint) or hint,
        datatype=_datatype_of(hint),
        extras=extras,
        optional=optional,
    )


def _datatype_of(hint: Any) -> Any:
    """The datatype an ``AttrR[int]``-style hint declares, or ``None``.

    ``None`` means the hint named an attribute class without saying what it
    holds, which is a promise rather than something the filler can build.
    """
    args = get_args(hint)
    return args[0] if len(args) == 1 else None


class ControllerFiller:
    """Creates and tracks the children a `Controller`'s class body declares.

    Every controller has one, as ``controller.filler``. It reads the class
    hints once, during construction, and holds what it found until
    `check_filled` reports on it.
    """

    def __init__(self, controller: BaseController) -> None:
        self._controller = controller
        self._declarations: dict[str, Declaration] = {}

    def read_hints(self) -> None:
        """Record what the class body declares, without creating anything yet.

        Called by ``BaseController.__init__`` before the ``@attr``/``@command``
        declarations are bound, so that what those bind can be checked against
        a hint of the same name.
        """
        from fastcs.controllers.base_controller import BaseController

        for raw_name, raw_hint in get_type_hints(
            type(self._controller), include_extras=True
        ).items():
            if raw_name.startswith("_") or raw_name == "root_attribute":
                # `root_attribute` is what a parent shows for this controller
                # rather than an attribute of it, and is declared on
                # `BaseController` itself, so it is not the filler's to create.
                continue

            hint = _unwrap(raw_hint)

            # What a hint declares is the class it subscripts, but not every
            # hint has one: `power: Annotated[AttrRW[float], spec] | AttrRW[int]`
            # unwraps to a `typing.Union`, and `power: dict[str, int]` to a
            # `dict`. The `issubclass` below is what rejects the second, and it
            # raises `TypeError` rather than returning False for the first, so
            # anything that is not a class is dropped before it gets there.
            if not isinstance(hint.declared_type, type):
                continue

            if not issubclass(hint.declared_type, Attribute | Method | BaseController):
                continue

            # ophyd-async's convention: a trailing underscore keeps a name that
            # would otherwise shadow a builtin or a framework member off the
            # class body, without renaming the attribute it declares.
            name = raw_name.removesuffix("_")

            self._declarations[name] = Declaration(
                name=name,
                raw_name=raw_name,
                hint=hint.type_,
                declared_type=hint.declared_type,
                datatype=hint.datatype,
                extras=hint.extras,
                optional=hint.optional,
            )

    def create_children_from_hints(self) -> None:
        """Create an unfilled `Attribute` for every hint that can produce one.

        Called once by ``BaseController.__init__``, after the class body's
        ``@attr`` declarations have been bound - a hint whose name one of those
        already provided is a check on it rather than something to create,
        which is how ADR 0018's decorated attributes and ADR 0013's hints share
        one class body.
        """
        for declaration in self._declarations.values():
            if not issubclass(declaration.declared_type, Attribute):
                continue

            if declaration.name in self._controller.attributes:
                continue

            self._create_attribute(declaration)

    def _create_attribute(self, declaration: Declaration) -> None:
        if declaration.datatype is None:
            # A hint that does not say what it holds cannot be built, only
            # promised. `state: AttrR` on an introspecting controller is the
            # motivating case - the enum's members are only known over the wire.
            return

        attr_type: type[Attribute] = declaration.declared_type
        attribute = attr_type(declaration.datatype)
        declaration.child = attribute
        self._controller.add_attribute(declaration.name, attribute)

    @property
    def declarations(self) -> dict[str, Declaration]:
        """Every class-body declaration this filler found, by name."""
        return self._declarations

    def __iter__(self) -> Iterator[tuple[Attribute | None, tuple[Any, ...]]]:
        """Yield ``(child, extras)`` for each declaration, as ADR 0013 asks.

        ``child`` is the unfilled `Attribute` where one could be created, and
        ``None`` for a promise. ``extras`` is whatever an ``Annotated`` hint
        carried, which is how a protocol library outside core FastCS - an SCPI
        package, say - gets its own declaration vocabulary without FastCS
        knowing anything about it.
        """
        for declaration in self._declarations.values():
            yield declaration.child, declaration.extras

    def fill_attribute(
        self,
        name: str,
        getter: Getter[DType_T] | Schedule[DType_T] | None = None,
        setter: Setter[DType_T] | None = None,
        datatype: type[DType_T] | None = None,
        **meta: Unpack[Meta],
    ) -> Attribute:
        """Provision a declared attribute with its IO and metadata.

        Args:
            name: The name the class body declared
            getter: IO to read the value with, optionally wrapped in a
                `Polled`/`NotPolled` schedule
            setter: IO to write the value with
            datatype: What the filling data says the attribute holds, checked
                against what the hint declared. Pass it when the source could
                disagree - introspection of a device that has changed under
                you - and leave it out when it cannot.
            meta: Metadata for the attribute, validated against the datatype
                the hint declared - ``precision`` on a ``str`` raises

        Returns:
            The attribute, which is the same object the hint created

        Raises:
            KeyError: If nothing of that name was declared
            TypeError: If the attribute has no half the given IO would fill,
                the datatype disagrees with the hint, or the metadata does not
                suit the datatype

        """
        declaration = self._declarations.get(name)
        if declaration is None:
            raise KeyError(
                f"{type(self._controller).__name__} has no attribute declaration "
                f"named '{name}' to fill. Declare it as a class-body hint with its "
                "datatype, or add the attribute with `add_attribute`."
            )

        if declaration.child is None:
            # A hint that does not name its datatype - `state: AttrR` - is a
            # promise rather than something the filler could build, so there is
            # no attribute here to provision.
            raise KeyError(
                f"{type(self._controller).__name__} declared '{name}' as "
                f"{declaration.hint} without a datatype, so there is no attribute "
                "to fill. Subscript the hint with the datatype it holds, or add "
                "the attribute with `add_attribute`."
            )

        attribute = declaration.child

        if datatype is not None and datatype != attribute.dtype:
            raise TypeError(
                f"Controller '{type(self._controller).__name__}' filled hinted "
                f"attribute '{name}' with the wrong datatype. Expected "
                f"'{attribute.dtype.__name__}', got "
                f"'{getattr(datatype, '__name__', datatype)}'."
            )

        if getter is not None:
            if not isinstance(attribute, AttrR):
                raise TypeError(
                    f"Attribute '{name}' was declared "
                    f"{type(attribute).__name__}, which has nothing to read."
                )
            attribute.set_getter(getter)

        if setter is not None:
            if not isinstance(attribute, AttrW):
                raise TypeError(
                    f"Attribute '{name}' was declared "
                    f"{type(attribute).__name__}, which has nothing to write."
                )
            attribute.set_setter(setter)

        if meta:
            # `update_meta` validates the fields against the datatype the hint
            # declared, which is the runtime counterpart to the static
            # `Unpack[FloatMeta]` check on the constructors.
            merged: Meta = {**attribute.meta, **meta}
            attribute.update_meta(merged)

        return attribute

    def fill_meta(self, name: str, meta: Meta) -> Attribute:
        """Fill a declared attribute's metadata from an extras object.

        The shape a protocol layer wants: ``SCPIParam(...).meta`` in one go,
        validated against the datatype the hint declared.
        """
        return self.fill_attribute(name, **meta)

    def check_filled(self) -> None:
        """Raise if anything the class body promised does not exist.

        A declaration the filler could create is satisfied by having been
        created, so what this reports is the promised-but-missing: a hint whose
        datatype was not knowable at author time, which introspection was
        therefore expected to add and did not. An ``| None`` hint is not
        required.

        Raises:
            RuntimeError: Listing, by name, what is still missing

        """
        missing: list[str] = []

        for name, declaration in self._declarations.items():
            if declaration.optional:
                continue

            declared = declaration.declared_type
            member = getattr(self._controller, name, None)
            if isinstance(member, declared):
                continue

            if member is None:
                missing.append(f"{name} (declared {declared.__name__}, never added)")
            else:
                missing.append(
                    f"{name} (declared {declared.__name__}, "
                    f"got {type(member).__name__})"
                )

        if not missing:
            return

        raise RuntimeError(
            f"Controller `{type(self._controller).__name__}` did not provision: "
            + ", ".join(sorted(missing))
        )
