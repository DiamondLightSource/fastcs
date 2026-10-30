"""Numeric limits, aligned with the bluesky event-model (ADR 0017)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

Numeric_T = TypeVar("Numeric_T", int, float)
"""A numeric type that can carry limits"""


@dataclass(frozen=True)
class Limits(Generic[Numeric_T]):
    """A pair of bounds on a numeric value.

    Either end may be ``None``, meaning unbounded in that direction.
    """

    low: Numeric_T | None = None
    """The lower bound, or ``None`` for unbounded"""
    high: Numeric_T | None = None
    """The upper bound, or ``None`` for unbounded"""

    def contains(self, other: Limits[Numeric_T]) -> bool:
        """Whether ``other`` lies within this range.

        An unbounded end of ``self`` contains any value; an unbounded end of
        ``other`` is only contained by an unbounded end of ``self``.
        """
        if self.low is not None and (other.low is None or other.low < self.low):
            return False
        if self.high is not None and (other.high is None or other.high > self.high):
            return False

        return True


UNBOUNDED: Limits = Limits()
"""Limits with neither end set"""


@dataclass(frozen=True)
class NumericLimits(Generic[Numeric_T]):
    """The four categories of limit on a numeric attribute.

    All four are optional and are resolved on construction, so that after
    construction every category holds a `Limits` - unbounded if nothing
    determined it. The rules (ADR 0017) are:

    - supply none and all four are unbounded;
    - a ``display`` range with no ``control`` range gives ``control`` the
      display range - what a device may be driven to defaults to what it is
      shown as spanning;
    - an ``alarm`` range with no ``warning`` range gives ``warning`` the alarm
      range;
    - supplying both asserts that ``warning`` lies within ``alarm``, since a
      warning outside the alarm range could never be the milder condition.

    >>> limits = NumericLimits(display=Limits(0.0, 10.0))
    >>> limits.control
    Limits(low=0.0, high=10.0)
    """

    control: Limits[Numeric_T] = UNBOUNDED
    """The range the attribute may be driven to"""
    display: Limits[Numeric_T] = UNBOUNDED
    """The range the attribute is displayed over"""
    alarm: Limits[Numeric_T] = UNBOUNDED
    """The range outside which the attribute is in alarm"""
    warning: Limits[Numeric_T] = UNBOUNDED
    """The range outside which the attribute is in a warning state"""

    def __post_init__(self) -> None:
        if self.control == UNBOUNDED and self.display != UNBOUNDED:
            object.__setattr__(self, "control", self.display)

        if self.warning == UNBOUNDED and self.alarm != UNBOUNDED:
            object.__setattr__(self, "warning", self.alarm)
        elif self.alarm != UNBOUNDED and not self.alarm.contains(self.warning):
            raise ValueError(
                f"Warning limits {self.warning} are not within alarm limits "
                f"{self.alarm}"
            )
