from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import String
from fastcs.launch import FastCS


class TemperatureController(Controller):
    device_id = AttrR(String())


fastcs = FastCS([ControllerSpec("DEMO", TemperatureController)], [])

if __name__ == "__main__":
    fastcs.run()
