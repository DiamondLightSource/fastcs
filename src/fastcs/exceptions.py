class FastCSError(Exception):
    """Base class for problems in running FastCS."""


class FatalError(FastCSError):
    """For when something has happened that means FastCS must stop, so that it can be
    restarted.
    """


class LaunchError(FastCSError):
    """For when there is an error in launching FastCS with the given
    transports and controller.
    """
