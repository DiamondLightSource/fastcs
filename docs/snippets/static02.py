from fastcs.controllers import Controller, ControllerSpec
from fastcs.launch import FastCS


class TemperatureController(Controller):
    pass


fastcs = FastCS(ControllerSpec("DEMO", TemperatureController), [])

if __name__ == "__main__":
    fastcs.run()
