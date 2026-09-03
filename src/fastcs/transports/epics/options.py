from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict


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
    mapping: Mapping[str, int | str | float | bool]


@dataclass
class EpicsCAOptions:
    """Channel-Access-specific options.

    Currently empty: present so ``epicsca:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future CA-specific knobs.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    aliases: Mapping[str, str | EnumMapping | list[str] | list[EnumMapping]] = field(
        default_factory=dict
    )
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
