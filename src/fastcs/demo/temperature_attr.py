"""Example 2 - getter/setter: per-attribute IO wired via callables in ``__init__``.

Baseline against the CURRENT callback-IO API. A **single** generic IO class
(``TemperatureIO``) drives every attribute; the per-attribute behaviour lives in
each attribute's ``TemperatureIORef``, which just carries the command-building
callables (``read_cmd``/``write_cmd``) taken from a single ``TemperatureProtocol``
class. This is the honest precursor to the ``AttrRW(getter=..., setter=...)``
constructor params landing in #392: ``read_cmd``/``write_cmd`` *are* the
getter/setter, and #392 simply promotes them onto the constructor and deletes
this IO/ref wrapper, while ``TemperatureProtocol`` survives unchanged. Contrast
with the composition example (``controllers.py``, #390), whose shared IO instead
dispatches on a ``name`` string.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import Float


@dataclass
class TemperatureAttrSettings:
    ip_settings: IPConnectionSettings


class TemperatureProtocol:
    """The device wire protocol - one method per command, referenced by the IORefs.

    Each getter returns the query string to send; each setter returns the command
    string to send for a given value. These are exactly the callables #392 will
    pass straight to ``AttrRW(getter=..., setter=...)``.
    """

    def get_ramp_rate(self) -> str:
        return "R?\r\n"

    def set_ramp_rate(self, value: float) -> str:
        return f"R={value}\r\n"

    def get_power(self) -> str:
        return "P?\r\n"


@dataclass
class TemperatureIORef(AttributeIORef):
    """Per-attribute IO spec: the command-building callables for one attribute."""

    read_cmd: Callable[[], str]
    write_cmd: Callable[[float], str] | None = None


class TemperatureIO(AttributeIO[float, TemperatureIORef]):
    """A single generic IO shared by every attribute; behaviour comes from the ref."""

    def __init__(self, connection: IPConnection):
        super().__init__()
        self._connection = connection

    async def update(self, attr: AttrR[float, TemperatureIORef]) -> None:
        response = await self._connection.send_query(attr.io_ref.read_cmd())
        await attr.update(float(response.strip("\r\n")))

    async def send(self, attr: AttrW[float, TemperatureIORef], value: float) -> None:
        if attr.io_ref.write_cmd is None:
            raise TypeError(f"{attr} is read-only: no write_cmd on its io_ref")
        await self._connection.send_command(attr.io_ref.write_cmd(value))


class TemperatureAttrController(Controller):
    """A small temperature controller wired attribute-by-attribute in ``__init__``."""

    def __init__(self, settings: TemperatureAttrSettings) -> None:
        self.connection = IPConnection()
        self._settings = settings
        self._protocol = TemperatureProtocol()

        super().__init__(ios=[TemperatureIO(self.connection)])

        self.ramp_rate = AttrRW(
            Float(),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_ramp_rate,
                write_cmd=self._protocol.set_ramp_rate,
                update_period=0.2,
            ),
        )
        self.power = AttrR(
            Float(),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_power,
                update_period=0.2,
            ),
        )

    async def connect(self) -> None:
        await self.connection.connect(self._settings.ip_settings)
        self._connected = True

    async def close(self) -> None:
        await self.connection.close()
        self._connected = False
