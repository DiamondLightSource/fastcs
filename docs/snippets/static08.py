from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR
from fastcs.connections import IPConnection
from fastcs.controllers import Controller, ControllerSpec
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
        value = await self._connection.query(query)

        await attr.update(attr.dtype(value))


class TemperatureController(Controller):
    device_id = AttrR(String(), io_ref=TemperatureControllerAttributeIORef("ID"))
    power = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef("P"))

    def __init__(self, connection: IPConnection):
        self._connection = connection

        super().__init__(ios=[TemperatureControllerAttributeIO(self._connection)])


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection = IPConnection("localhost", 25565)
spec = ControllerSpec("DEMO", TemperatureController, connection=connection)
fastcs = FastCS([spec], [epics_ca])

if __name__ == "__main__":
    fastcs.run()
