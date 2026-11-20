from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import Float, String
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport

NumberT = TypeVar("NumberT", int, float)


@dataclass
class TemperatureControllerAttributeIORef(AttributeIORef):
    name: str
    _: KW_ONLY
    update_period: float | None = 0.2


class TemperatureControllerAttributeIO(
    AttributeIO[NumberT, TemperatureControllerAttributeIORef]
):
    def __init__(self, connection: IPConnection):
        super().__init__()

        self._connection = connection

    async def update(self, attr: AttrR[NumberT, TemperatureControllerAttributeIORef]):
        query = f"{attr.io_ref.name}?"
        response = await self._connection.send_query(f"{query}\r\n")
        value = response.strip("\r\n")

        await attr.update(attr.dtype(value))

    async def send(
        self, attr: AttrW[NumberT, TemperatureControllerAttributeIORef], value: NumberT
    ) -> None:
        command = f"{attr.io_ref.name}={attr.dtype(value)}"
        await self._connection.send_command(f"{command}\r\n")


class TemperatureController(Controller):
    device_id = AttrR(String(), io_ref=TemperatureControllerAttributeIORef("ID"))
    power = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef("P"))
    ramp_rate = AttrRW(Float(), io_ref=TemperatureControllerAttributeIORef("R"))

    def __init__(self, settings: IPConnectionSettings, *, path=None):
        self._ip_settings = settings
        self._connection = IPConnection()

        super().__init__(
            ios=[TemperatureControllerAttributeIO(self._connection)], path=path
        )

    async def connect(self):
        await self._connection.connect(self._ip_settings)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(connection_settings, path=["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
