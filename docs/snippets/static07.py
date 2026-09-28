from pathlib import Path

from fastcs.attributes import AttrR, Polled
from fastcs.connections import IPConnection, IPConnectionSettings, Supervisor
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    connection: IPConnection

    def __init__(self, connection: IPConnection):
        self.connection = connection

        super().__init__()

        self.device_id = AttrR(str, getter=Polled(self._get_device_id, period=0.2))

    async def _get_device_id(self) -> str:
        response = await self.connection.send_query("ID?\r\n")
        return response.strip("\r\n")


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
supervisor = Supervisor(IPConnection(connection_settings), name="temperature")
controller = TemperatureController(supervisor.handle)
controller.set_path(["DEMO"])
# The supervisor opens the connection, reconnects it if it drops, and hands the
# controller a handle that behaves like the IPConnection itself.
fastcs = FastCS(controller, [epics_ca], supervisors=[supervisor])

if __name__ == "__main__":
    fastcs.run()
