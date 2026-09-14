"""`AttrFactory` - binds an `AttrBackend`'s IO onto attributes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, cast

from fastcs.attributes.attr_r import AttrR, Getter, NotPolled, Polled, Schedule
from fastcs.attributes.attr_rw import AttrRW
from fastcs.attributes.attr_w import AttrW, Setter
from fastcs.attributes.attribute import Attribute
from fastcs.attributes.backend import AttrBackend, Ts
from fastcs.attributes.update import Update
from fastcs.datatypes import DType_T


@dataclass
class AttrFactory(Generic[*Ts, DType_T]):
    """Binds a backend's IO onto attributes - new ones, or ones already declared."""

    backend: AttrBackend[*Ts, DType_T]

    def attr_r(
        self,
        datatype: type[DType_T],
        *args: *Ts,
        schedule: Schedule[DType_T] | None = None,
    ) -> AttrR[DType_T]:
        """Build a new read-only attribute, its getter bound to this backend."""
        getter = self._schedule(*args, schedule=schedule)
        attribute = AttrR(cast(Any, datatype), getter=getter)
        return cast(AttrR[DType_T], attribute)

    def attr_w(self, datatype: type[DType_T], *args: *Ts) -> AttrW[DType_T]:
        """Build a new write-only attribute, its setter bound to this backend."""
        attribute = AttrW(cast(Any, datatype), setter=self._setter(*args))
        return cast(AttrW[DType_T], attribute)

    def attr_rw(
        self,
        datatype: type[DType_T],
        *args: *Ts,
        schedule: Schedule[DType_T] | None = None,
    ) -> AttrRW[DType_T]:
        """Build a new read-write attribute, its getter/setter bound to this backend."""
        attribute = AttrRW(
            cast(Any, datatype),
            getter=self._schedule(*args, schedule=schedule),
            setter=self._setter(*args),
        )
        return cast(AttrRW[DType_T], attribute)

    def fill(
        self,
        attr: Attribute[DType_T],
        *args: *Ts,
        schedule: Schedule[DType_T] | None = None,
    ) -> None:
        """Bind this backend's get/set onto an already-constructed attribute.

        Args:
            attr: The already-constructed attribute to bind IO onto
            args: Forwarded to the backend's `get`/`set` to identify the resource
            schedule: A bare `Polled(period=...)`/`NotPolled()` to read the getter
                on, or `None` to read once, at connect

        """
        if isinstance(attr, AttrR):
            attr.set_getter(self._schedule(*args, schedule=schedule))
        if isinstance(attr, AttrW):
            attr.set_setter(self._setter(*args))

    def _schedule(
        self, *args: *Ts, schedule: Schedule[DType_T] | None
    ) -> Getter[DType_T] | Schedule[DType_T]:
        """This backend's getter, with `schedule` applied.

        `Polled`/`NotPolled` bind a getter onto themselves when called (the
        same mechanism `AttrR.declare`'s own `schedule` argument uses), so
        there is no `Polled`/`NotPolled` branch to write here.
        """
        getter = self._getter(*args)

        if isinstance(schedule, Polled | NotPolled) and schedule.getter is not None:
            raise TypeError(
                "The schedule given to `AttrFactory` already has a getter; pass "
                "a bare Polled(period=...) or NotPolled()"
            )

        return getter if schedule is None else schedule(getter)

    def _getter(self, *args: *Ts) -> Getter[DType_T]:
        async def get() -> DType_T:
            return await self.backend.get(*args)

        return get

    def _setter(self, *args: *Ts) -> Setter[DType_T]:
        async def set_(value: DType_T) -> DType_T | Update[DType_T] | None:
            return await self.backend.set(value, *args)

        return set_
