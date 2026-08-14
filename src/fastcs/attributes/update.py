from __future__ import annotations

from dataclasses import dataclass
from typing import Generic

from fastcs.attributes.severity import Severity
from fastcs.datatypes import DType_T


@dataclass
class Update(Generic[DType_T]):
    """A value returned from a getter or setter, with optional metadata.

    A getter or setter may return a bare value, or wrap it in ``Update`` to say
    more about it:

    - ``timestamp`` - when the value was obtained. ``None`` means the framework
      should stamp it with the time the update was received.
    - ``severity`` - how wrong the value is, if the source says. ``None`` means
      the source did not report one, which is read as ``Severity.NO_ALARM``.
    - ``setpoint`` - a setpoint to publish alongside the readback. ``None`` leaves
      the cached setpoint untouched.

    A bare value returned from a setter is equivalent to
    ``Update(readback=value, setpoint=value)`` - the device's accepted or clamped
    value, which is both what it will report and what was asked of it.

    The value/timestamp/severity trio follows the shape of bluesky's ``Reading``
    so the two read the same way, but shares no code with it.
    """

    readback: DType_T
    timestamp: float | None = None
    setpoint: DType_T | None = None
    severity: Severity | None = None
