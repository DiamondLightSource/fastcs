from pathlib import Path

from fastcs.attributes import AttrR
from fastcs.connections import IPConnection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    device_id = AttrR(String())

    def __init__(self, connection: IPConnection):
        super().__init__()

        self._connection = connection


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection = IPConnection("localhost", 25565)
spec = ControllerSpec("DEMO", TemperatureController, connection)
fastcs = FastCS(spec, [epics_ca])


if __name__ == "__main__":
    fastcs.run()
