from pathlib import Path

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureController(Controller):
    device_id = AttrR(str)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
controller = TemperatureController()
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
