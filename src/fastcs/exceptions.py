class FastCSError(Exception):
    """Base class for general problems in the running of a FastCS transport."""


class LaunchError(FastCSError):
    """For when there is an error in launching FastCS with the given
    transports and controller.
    """


class DisconnectedError(ConnectionError):
    """The link to a device is down, so the call could not be made.

    Raised by a connection's `Supervisor` rather than by driver code: every call
    through a connection handle passes the supervisor, which fails fast while the
    link is down and turns a link failure into this. A driver may raise it
    directly for a device-specific "I'm offline" signal, such as a special reply,
    and the supervisor treats that as a link failure too.

    A `ConnectionError`, and so an `OSError`, because that is what it is: the
    transport is gone, not the device complaining.
    """
