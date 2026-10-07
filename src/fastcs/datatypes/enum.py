import enum
from dataclasses import dataclass
from functools import cached_property
from typing import Generic, TypeVar

from fastcs.datatypes.datatype import DataType

Enum_T = TypeVar("Enum_T", bound=enum.Enum)
"""A builtin Enum type"""


@dataclass(frozen=True)
class Enum(Generic[Enum_T], DataType[Enum_T]):
    enum_cls: type[Enum_T]

    def __post_init__(self):
        if not issubclass(self.enum_cls, enum.Enum):
            raise ValueError("Enum class has to take an Enum.")

    def index_of(self, value: Enum_T) -> int:
        return self.members.index(value)

    @cached_property
    def members(self) -> list[Enum_T]:
        return list(self.enum_cls)

    @cached_property
    def names(self) -> list[str]:
        return [member.name for member in self.members]

    @property
    def dtype(self) -> type[Enum_T]:
        return self.enum_cls

    @property
    def initial_value(self) -> Enum_T:
        return self.members[0]


def _value_mixin(enum_cls: type[enum.Enum]) -> type | None:
    """The builtin type an enum's members compare as.

    Returns ``str`` if ``enum_cls`` is a StrEnum, or ``int`` if it is an IntEnum
    """
    for mixin in (str, int):
        if issubclass(enum_cls, mixin):
            return mixin

    return None


def check_enum_hint(hinted: type[enum.Enum], actual: type[enum.Enum]) -> None:
    """Check that the enum of an `Attribute` satisfies the enum in its type hint.

    The hint is satisfied if:

    - It is the same class (e.g., using an enum registry for introspection to pull from)
    - It is a member-less enum base, such as `enum.StrEnum`, and ``actual``
      derives from it (e.g., ``actual`` is a `enum.StrEnum` with members)
    - Every value in ``hinted`` is also a value of ``actual``, and both share the same
      ``str`` or ``int`` mixin.

    Args:
        hinted: The enum class in the type hint
        actual: The enum class the `Attribute` holds

    Raises:
        ValueError: Describing why the hint is not satisfied

    """
    if hinted is actual:  # The same enum class is used
        return

    if len(hinted.__members__) == 0:  # This is a member-less enum
        if issubclass(actual, hinted):  # Actual enum derived from the member-less enum
            return
        raise ValueError(
            f"'{actual.__name__}' is not a '{hinted.__name__}'"
        )  # Raise as different kinds of enums

    hinted_mixin = _value_mixin(hinted)
    if hinted_mixin is None:
        # In this case, we have a defined enum.Enum subclass, but hinted is not actual,
        # so it can only match itself.
        raise ValueError(
            f"'{hinted.__name__}' has members but no str or int mixin, so it only "
            "matches itself - use enum.StrEnum or enum.IntEnum to match an "
            "introspected enum by value"
        )

    actual_mixin = _value_mixin(actual)
    if actual_mixin is not hinted_mixin:
        actual_kind = actual_mixin.__name__ if actual_mixin else "no"
        # In this case, we have a defined enum.StrEnum or enum.IntEnum subclass,
        # but the actual enum is of a different enum type.
        raise ValueError(
            f"'{hinted.__name__}' has {hinted_mixin.__name__} values but "
            f"'{actual.__name__}' has {actual_kind} values"
        )

    actual_values = {member.value for member in actual}
    # A hinted enum_cls can have a subset of the members the actual enum_cls has,
    # but must not contain any extra members.
    missing = [member.value for member in hinted if member.value not in actual_values]
    if missing:
        raise ValueError(
            f"'{hinted.__name__}' has values that '{actual.__name__}' does not: "
            f"{', '.join(repr(value) for value in missing)}"
        )
