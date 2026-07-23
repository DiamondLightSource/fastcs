from __future__ import annotations

from dataclasses import dataclass
from typing import Generic

from fastcs.datatypes import DType_T


@dataclass
class Update(Generic[DType_T]):
    """A value returned from a getter or setter, with optional metadata.

    A getter/setter may return a bare value, or wrap it in ``Update`` to also
    carry the timestamp the value was obtained at (``None`` means the framework
    should stamp receive-time).
    """

    value: DType_T
    timestamp: float | None = None
