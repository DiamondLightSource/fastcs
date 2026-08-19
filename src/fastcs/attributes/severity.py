from enum import Enum


class Severity(Enum):
    """How wrong a value is, if at all.

    A FastCS-native enum that happens to use the same strings as EPICS alarm
    severities, so a driver or transport speaking EPICS does not have to
    translate. It is not EPICS-specific: a Tango event push or any other IO can
    report severity the same way, through `Update`.
    """

    NO_ALARM = "NO_ALARM"
    """The value is good"""
    MINOR = "MINOR"
    """The value is outside its warning range, or the device reports a minor fault"""
    MAJOR = "MAJOR"
    """The value is outside its alarm range, or the device reports a major fault"""
    INVALID = "INVALID"
    """The value could not be read, or cannot be trusted"""
