from pathlib import Path

from fastcs.attributes import AttrR, Polled
from fastcs.connections import Connections, IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    connection: IPConnection

    def __init__(self, settings: IPConnectionSettings):
        self.connection = IPConnection(settings)

        super().__init__()

        self.device_id = AttrR(str, getter=Polled(self._get_device_id, period=0.2))

    async def _get_device_id(self) -> str:
        response = await self.connection.send_query("ID?\r\n")
        return response.strip("\r\n")


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(connection_settings)
controller.set_path(["DEMO"])
# Every connection an application runs is declared up front, so the runner can
# open them all before it walks the tree.
connections = Connections({"temperature": controller.connection})
fastcs = FastCS(controller, [epics_ca], connections=connections)

if __name__ == "__main__":
    fastcs.run()
