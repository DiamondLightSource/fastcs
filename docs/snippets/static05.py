from pathlib import Path

from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    device_id = AttrR(String())


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
spec = ControllerSpec("DEMO", TemperatureController)
fastcs = FastCS([spec], [epics_ca])

if __name__ == "__main__":
    fastcs.run()
