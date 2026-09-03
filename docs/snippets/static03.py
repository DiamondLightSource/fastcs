from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.launch import FastCS


class TemperatureController(Controller):
    device_id = AttrR(str)


fastcs = FastCS(TemperatureController(), [])

if __name__ == "__main__":
    fastcs.run()
