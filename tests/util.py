import enum

from fastcs.datatypes import StrictEnum, SubsetEnum, SupersetEnum


class ColourEnum(enum.IntEnum):
    RED = 0
    GREEN = 1
    BLUE = 2


class Mode(StrictEnum):
    """Device choices must exactly match."""

    OFF = "Off"
    ON = "On"


class Gain(SupersetEnum):
    """Device lacks AUTO, which only exists on the setpoint."""

    AUTO = "Auto"
    LOW = "Low"
    HIGH = "High"


class Trigger(str, SubsetEnum):
    """Device has extra trigger sources we don't care about."""

    INTERNAL = "Internal"
    EXTERNAL = "External"


# Choices as a device would report them during introspection
DeviceMode = enum.Enum("DeviceMode", {"OFF": "Off", "ON": "On"})
DeviceGain = enum.Enum("DeviceGain", {"LOW": "Low", "HIGH": "High"})
DeviceTrigger = enum.Enum(
    "DeviceTrigger",
    {"INTERNAL": "Internal", "EXTERNAL": "External", "SOFTWARE": "Software"},
)
