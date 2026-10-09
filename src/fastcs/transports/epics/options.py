from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar, TypeAlias

from pydantic import ConfigDict

EnumMappingValue: TypeAlias = bool | int | float | str


@dataclass
class EpicsDocsOptions:
    """Docs options for EPICS."""

    output_dir: Path = Path(".")
    title: str = "FastCS Devices"
    depth: int | None = None


class EpicsGUIFormat(Enum):
    """The format of an EPICS GUI."""

    bob = ".bob"
    edl = ".edl"


@dataclass
class EpicsGUIOptions:
    """Epics GUI options for use in both CA and PVA transports."""

    output_dir: Path = Path(".")
    file_format: EpicsGUIFormat = EpicsGUIFormat.bob
    title: str = "FastCS Devices"


@dataclass
class EnumMapping:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    pv: str
    mapping: dict[str, EnumMappingValue]


@dataclass
class EpicsCAOptions:
    """Channel-Access-specific options.

    Currently empty: present so ``epicsca:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future CA-specific knobs.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    aliases: dict[str, str | EnumMapping] = field(default_factory=dict)
    """Mapping of fastcs PV names to their aliases.

    Setpoint and readback PVs must be aliased separately.
    """


@dataclass
class EpicsPVAOptions:
    """PVAccess-specific options.

    Currently empty: present so ``epicspva:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future PVA-specific knobs.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
