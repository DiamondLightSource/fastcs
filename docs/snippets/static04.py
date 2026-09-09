from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics.ca.transport import EpicsCATransport


class TemperatureController(Controller):
    device_id: AttrR[str]


epics_ca = EpicsCATransport()
controller = TemperatureController()
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
