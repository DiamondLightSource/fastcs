import enum
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Generic, TypeVar

from fastcs.datatypes.datatype import DataType

Enum_T = TypeVar("Enum_T", bound=enum.Enum)
"""A builtin Enum type"""


@dataclass(frozen=True)
class Enum(Generic[Enum_T], DataType[Enum_T]):
    enum_cls: type[Enum_T]
    allowed: type[enum.Enum] | None = None
    """Introspected device enum restricting (for SupersetEnum)
      or extending (for SubsetEnum) the choices of ``enum_cls``, mapped by value"""

    def __post_init__(self):
        if not issubclass(self.enum_cls, enum.Enum):
            raise ValueError("Enum class has to take an Enum.")

    def index_of(self, value: Enum_T) -> int:
        return self.members.index(value)

    @cached_property
    def members(self) -> list[Enum_T]:
        if self.allowed is None:
            return list(self.enum_cls)
        # SubsetEnum returns the raw value for device choices it doesn't define
        return [self.enum_cls(device_member.value) for device_member in self.allowed]

    @cached_property
    def names(self) -> list[str]:
        if self.allowed is None:
            return [member.name for member in self.members]
        return [
            choice.name if isinstance(choice, self.enum_cls) else device_member.name
            for choice, device_member in zip(self.members, self.allowed, strict=True)
        ]

    def validate(self, value: Any) -> Enum_T:
        # Map members of another enum, e.g. an introspected one, by value
        if isinstance(value, enum.Enum) and not isinstance(value, self.enum_cls):
            value = value.value

        value = super().validate(value)
        if value not in self.members:
            raise ValueError(f"{value!r} is not one of the choices {self.names}")
        return value

    @property
    def dtype(self) -> type[Enum_T]:
        return self.enum_cls

    @property
    def initial_value(self) -> Enum_T:
        return self.members[0]


class StrictEnum(enum.Enum):
    """All members should exist on the device, and there will be no extras."""


class _SubsetEnumMeta(enum.EnumMeta):
    _member_type_: type
    """The mixed in value type, e.g. ``str``, set by ``EnumMeta``"""

    def __new__(mcls, *args, **kwargs):
        cls = super().__new__(mcls, *args, **kwargs)
        if cls._member_map_ and cls._member_type_ is object:
            raise TypeError(
                f"{cls.__name__} needs a value type, e.g. (str, SubsetEnum)"
            )
        return cls

    def __call__(cls, value, names=None, *args, **kwargs):  # type: ignore
        """Return the member for ``value``, or ``value`` itself if it is of the
        member type but not one of the members."""
        if names is not None:  # functional API
            return super().__call__(value, names, *args, **kwargs)
        try:
            return super().__call__(value)
        except ValueError:
            if isinstance(value, cls._member_type_):
                return value
            raise


class SubsetEnum(enum.Enum, metaclass=_SubsetEnumMeta):
    """All members should exist on the device, but the device may have extras.

    Subclasses must mix in a value type, e.g. ``class Trigger(str, SubsetEnum)``, as
    values for device choices outside the enum are kept as that raw type.
    """


class SupersetEnum(enum.Enum):
    """Some members should exist on the device, and there should be no extras."""


EnumTypes = (StrictEnum, SubsetEnum, SupersetEnum)


def validate_enum_choices(
    hinted: type[enum.Enum], introspected: type[enum.Enum]
) -> None:
    """Check introspected enum choices against a hinted enum, compared by value.

    Raises:
        TypeError: If the choices are not compatible with the hinted enum type

    """
    choices = [member.value for member in hinted]
    device_choices = [member.value for member in introspected]
    error_msg = (
        f"Device has choices {device_choices}, but {hinted.__name__} requested "
        f"{choices} "
    )
    if issubclass(hinted, StrictEnum):
        if set(choices) != set(device_choices):
            raise TypeError(error_msg + "to be strictly equal to them.")
    elif issubclass(hinted, SubsetEnum):
        if not set(choices).issubset(device_choices):
            raise TypeError(error_msg + "to be a subset of them.")
    elif issubclass(hinted, SupersetEnum):
        if not set(device_choices).issubset(choices):
            raise TypeError(error_msg + "to be a superset of them.")
    else:
        raise TypeError(
            f"{hinted.__name__} is not a StrictEnum, SubsetEnum, or SupersetEnum"
        )
