from pathlib import Path

from fastcs.attributes import AttrR
from fastcs.connections import IPConnection, IPConnectionSettings, Supervisor
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    connection: IPConnection

    device_id: AttrR[str]

    def __init__(self, connection: IPConnection):
        super().__init__()

        self.connection = connection


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
