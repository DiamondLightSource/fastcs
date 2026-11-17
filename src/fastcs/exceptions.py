class FastCSError(Exception):
    """Base class for problems in running FastCS."""


class ReconnectFailedError(FastCSError):
    """Raised when a connection doesn't reconnect within its ``reconnect_attempts``"""


class IntrospectionMismatchError(FastCSError):
    """Raised when a connection's introspection changes structure on reconnect"""


class LaunchError(FastCSError):
    """For when there is an error in launching FastCS with the given
    transports and controller.
    """
