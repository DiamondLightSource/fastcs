from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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
class EpicsCAOptions:
    """Channel-Access-specific options.

    Currently empty: present so ``epicsca:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future CA-specific knobs.
    """


@dataclass
class EpicsPVAOptions:
    """PVAccess-specific options.

    Currently empty: present so ``epicspva:`` survives in fastcs.yaml as the
    transport discriminator key. Reserved for future PVA-specific knobs.
    """
