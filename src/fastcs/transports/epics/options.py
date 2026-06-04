from dataclasses import dataclass
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
    title: str | None = None
    """Index screen title.

    ``None`` (default): use the root controller PV prefix, or ``"FastCS Devices"``
    if no root controller is present.  Set to ``""`` to suppress the title widget
    entirely.

    Note: titles longer than about 30 characters may be truncated in the
    generated ``.bob`` screen."""


@dataclass
class EpicsCAOptions:
    """Channel-Access-specific options.

    Currently empty: present so ``epicsca:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future CA-specific knobs.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


@dataclass
class EpicsPVAOptions:
    """PVAccess-specific options.

    Currently empty: present so ``epicspva:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future PVA-specific knobs.
    """

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
