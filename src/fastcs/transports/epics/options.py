from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass
class EpicsDocsOptions:
    """Docs options for EPICS."""

    path: Path = Path(".")
    depth: int | None = None


class EpicsGUIFormat(Enum):
    """The format of an EPICS GUI."""

    bob = ".bob"
    edl = ".edl"


@dataclass
class EpicsGUIOptions:
    """Epics GUI options for use in both CA and PVA transports."""

    output_path: Path = Path(".") / "output.bob"
    file_format: EpicsGUIFormat = EpicsGUIFormat.bob
    title: str = "Simple Device"


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
