from .connection import Connection as Connection
from .connection import DisconnectedError as DisconnectedError
from .ip_connection import IPConnection as IPConnection

try:
    from .http_connection import HTTPConnection as HTTPConnection
except ImportError:
    pass

try:
    from .serial_connection import SerialConnection as SerialConnection
except ImportError:
    pass
