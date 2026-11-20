from typing import NoReturn

from fastcs.util import optional_dependency

from .builtin.ip_connection import IPConnection as IPConnection
from .connection import Connection as Connection
from .connection import ConnectionState as ConnectionState
from .connection import DisconnectedError as DisconnectedError
from .connection import Introspection as Introspection
from .connection import disconnect_on as disconnect_on
from .connection_spec import ConnectionSpec as ConnectionSpec

with optional_dependency("httpx"):
    from .builtin.http_connection import HTTPConnection as HTTPConnection

with optional_dependency("aioserial"):
    from .builtin.serial_connection import SerialConnection as SerialConnection

# The extra that installs each optional connection
_EXTRAS = {"HTTPConnection": "http", "SerialConnection": "serial"}


def __getattr__(name: str) -> NoReturn:
    """Name the extra to install when an optional connection is missing.

    Raises:
        ImportError: If ``name`` is an optional connection whose extra isn't installed
        AttributeError: If ``name`` isn't in this module

    """
    if name in _EXTRAS:
        extra = _EXTRAS[name]
        raise ImportError(
            f'{name} needs the {extra} extra: pip install "fastcs[{extra}]"'
        )

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
