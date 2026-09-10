from fastcs.connections.connection import Connection
from fastcs.logging import logger


class SimConnection(Connection):
    """Base for simulated connections.

    Opens and closes trivially and can never fail, so it is connected for the life of
    the process and its reconnect task idles forever. Subclasses implement whatever IO
    methods their driver calls, and never call ``set_disconnected()`` - there is no
    transport that can go away.

    A sibling of the real transports rather than a subclass of one: a simulator that
    inherits `IPConnection` also inherits a stream handle it will never open and a
    property that raises. Inheriting this instead means the only thing a driver
    writes is the pretending::

        class SimSerialConnection(SimConnection):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self._position = 0

            async def send_query(self, message: bytes, response_size: int) -> bytes:
                ...  # canned responses

    Which one an application gets is decided by ``type:`` in ``fastcs.yaml``, not by
    a magic port value or an environment check, so real and simulated hardware are
    interchangeable in config with no change to driver code.
    """

    async def connect(self) -> None:
        logger.info("[SIM] Connected", connection=type(self).__name__)

    async def close(self) -> None:
        logger.info("[SIM] Disconnected", connection=type(self).__name__)
