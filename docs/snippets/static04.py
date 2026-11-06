from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import String
from fastcs.launch import FastCS
from fastcs.transports.epics.ca.transport import EpicsCATransport


class TemperatureController(Controller):
    device_id = AttrR(String())


epics_ca = EpicsCATransport()
spec = ControllerSpec("DEMO", TemperatureController)
fastcs = FastCS(spec, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
