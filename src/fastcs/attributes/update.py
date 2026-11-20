from __future__ import annotations

from dataclasses import dataclass
from typing import Generic

from fastcs.datatypes import DType_T


@dataclass
class Update(Generic[DType_T]):
    """A value returned from a getter or setter, with optional metadata.

    A getter or setter may return a bare value, or wrap it in ``Update`` to say
    more about it:

    - ``timestamp`` - when the value was obtained. Not yet used: it is accepted so
      that the field order is settled, and is dropped until native timestamps
      are supported.
    - ``setpoint`` - a setpoint to publish alongside the readback. ``None`` leaves
      the cached setpoint untouched.

    A bare value returned from a setter is equivalent to
    ``Update(readback=value, setpoint=value)`` - the device's accepted or clamped
    value, which is both what it will report and what was asked of it.
    """

    readback: DType_T
    timestamp: float | None = None
    setpoint: DType_T | None = None
