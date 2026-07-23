"""Example 2 - getter/setter: per-attribute IO wired directly in ``__init__``.

Baseline against the CURRENT callback-IO API (deliberately messy): each attribute
gets its own small ``AttributeIO``/``AttributeIORef`` pair, closing directly over the
command it queries/commands on the temperature sim, and attributes are assigned in
``__init__`` rather than declared in the class body. This foreshadows the
``AttrRW(getter=..., setter=...)`` constructor params landing in #392, without a
shared IO class dispatching by name (contrast with the composition example,
``controllers.py``, #390).
"""

from dataclasses import dataclass

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import Float


@dataclass
class TemperatureAttrSettings:
    ip_settings: IPConnectionSettings


class RampRateIORef(AttributeIORef):
    pass


class RampRateIO(AttributeIO[float, RampRateIORef]):
    """IO for the ramp rate attribute only - a fresh instance per attribute."""

    def __init__(self, connection: IPConnection):
        super().__init__()
        self._connection = connection

    async def update(self, attr: AttrR[float, RampRateIORef]) -> None:
        response = await self._connection.send_query("R?\r\n")
        await attr.update(attr.dtype(response.strip("\r\n")))

    async def send(self, attr: AttrW[float, RampRateIORef], value: float) -> None:
        await self._connection.send_command(f"R={attr.dtype(value)}\r\n")


class PowerIORef(AttributeIORef):
    pass


class PowerIO(AttributeIO[float, PowerIORef]):
    """IO for the power attribute only - a fresh instance per attribute."""

    def __init__(self, connection: IPConnection):
        super().__init__()
        self._connection = connection

    async def update(self, attr: AttrR[float, PowerIORef]) -> None:
        response = await self._connection.send_query("P?\r\n")
        await attr.update(attr.dtype(response.strip("\r\n")))


class TemperatureAttrController(Controller):
    """A small temperature controller wired attribute-by-attribute in ``__init__``."""

    def __init__(self, settings: TemperatureAttrSettings) -> None:
        self.connection = IPConnection()
        self._settings = settings

        super().__init__(ios=[RampRateIO(self.connection), PowerIO(self.connection)])

        self.ramp_rate = AttrRW(Float(), io_ref=RampRateIORef(update_period=0.2))
        self.power = AttrR(Float(), io_ref=PowerIORef(update_period=0.2))

    async def connect(self) -> None:
        await self.connection.connect(self._settings.ip_settings)
        self._connected = True

    async def close(self) -> None:
        await self.connection.close()
        self._connected = False
