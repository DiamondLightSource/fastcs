from pathlib import Path

from fastcs.attributes import AttrR
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    def __init__(self, settings: IPConnectionSettings):
        self._ip_settings = settings
        self._connection = IPConnection()

        super().__init__()

        self.device_id = AttrR(String(), getter=self._get_device_id, poll_period=0.2)

    async def _get_device_id(self) -> str:
        response = await self._connection.send_query("ID?\r\n")
        return response.strip("\r\n")

    async def connect(self):
        await self._connection.connect(self._ip_settings)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(connection_settings)
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
