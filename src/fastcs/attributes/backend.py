"""`AttrBackend` - a protocol-agnostic get/set for one attribute's value."""

from __future__ import annotations

from typing import Protocol, TypeVarTuple

from fastcs.attributes.update import Update
from fastcs.datatypes import DType_T

Ts = TypeVarTuple("Ts")
"""The positional arguments a backend's `get`/`set` identify a resource by"""


class AttrBackend(Protocol[*Ts, DType_T]):
    """Something that can `get`/`set` a value given protocol-specific arguments.

    One instance typically serves every attribute a controller declares. `args` is how
    a single call says *which* attribute it means.
    """

    async def get(self, *args: *Ts) -> DType_T:
        """Read the value identified by `args`."""
        ...

    async def set(self, value: DType_T, *args: *Ts) -> DType_T | Update[DType_T] | None:
        """Write `value` to the resource identified by `args`.

        Mirrors `Setter[DType_T]` (ADR 0014): `None` is fire-and-forget, and a
        returned value or `Update` is the device's accepted/clamped value, applied
        to the attribute's readback and setpoint immediately.
        """
        ...
