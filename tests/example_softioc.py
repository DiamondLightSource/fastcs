from pathlib import Path

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Int
from fastcs.methods import command
from fastcs.transports.epics.ca.transport import EpicsCATransport, EpicsGUIOptions


class ParentController(Controller):
    a: AttrR = AttrR(Int())
    b: AttrRW = AttrRW(Int())


class ChildController(Controller):
    c: AttrW = AttrW(Int())

    @command()
    async def d(self):
        pass


def run(id="SOFTIOC_TEST_DEVICE"):
    controller = ParentController(path=[id])
    vector_path = [id, "ChildVector"]
    vector = ControllerVector(
        {i: ChildController(path=vector_path + [str(i)]) for i in range(2)},
        path=vector_path,
    )
    controller.add_sub_controller("ChildVector", vector)
    gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Vector")
    fastcs = FastCS(
        controller,
        [EpicsCATransport(gui=gui_options)],
    )
    fastcs.run(interactive=False)


if __name__ == "__main__":
    run()
