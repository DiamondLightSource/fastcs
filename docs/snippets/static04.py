from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics.ca.transport import EpicsCATransport


class TemperatureController(Controller):
    device_id = AttrR(String())


epics_ca = EpicsCATransport()
controller = TemperatureController("DEMO")
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
