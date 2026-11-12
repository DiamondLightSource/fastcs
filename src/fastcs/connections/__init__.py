from fastcs.util import optional_dependency

from .connection import Connection as Connection
from .connection import DisconnectedError as DisconnectedError
from .ip_connection import IPConnection as IPConnection

with optional_dependency("httpx"):
    from .http_connection import HTTPConnection as HTTPConnection

with optional_dependency("aioserial"):
    from .serial_connection import SerialConnection as SerialConnection
