from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR
from fastcs.connections import IPConnection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport

NumberT = TypeVar("NumberT", int, float)


@dataclass
class IDAttributeIORef(AttributeIORef):
    update_period: float | None = 0.2


class IDAttributeIO(AttributeIO[NumberT, IDAttributeIORef]):
    def __init__(self, connection: IPConnection):
        super().__init__()

        self._connection = connection

    async def update(self, attr: AttrR[NumberT, IDAttributeIORef]):
        response = await self._connection.send_query("ID?\r\n")
        value = response.strip("\r\n")

        await attr.update(attr.dtype(value))


class TemperatureController(Controller):
    device_id = AttrR(String(), io_ref=IDAttributeIORef())

    def __init__(self, connection: IPConnection):
        self._connection = connection

        super().__init__(ios=[IDAttributeIO(self._connection)])


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection = IPConnection("localhost", 25565)
spec = ControllerSpec("DEMO", TemperatureController, connection=connection)
fastcs = FastCS([spec], [epics_ca])

if __name__ == "__main__":
    fastcs.run()
