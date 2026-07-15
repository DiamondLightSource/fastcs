import asyncio
from pathlib import Path

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Int
from fastcs.methods import command
from fastcs.transports.epics.ca.transport import (
    EpicsCAOptions,
    EpicsCATransport,
    EpicsGUIOptions,
)


class ParentController(Controller):
    a: AttrR = AttrR(Int())
    b: AttrRW = AttrRW(Int())


class ChildController(Controller):
    fail_on_next_e: bool = True
    c: AttrW = AttrW(Int())

    @command()
    async def d(self):
        print("D: RUNNING")
        await asyncio.sleep(0.1)
        if self.fail_on_next_e:
            self.fail_on_next_e = False
            raise RuntimeError("D: FAILED WITH THIS WEIRD ERROR")
        else:
            self.fail_on_next_e = True
            print("D: FINISHED")


def run(id="SOFTIOC_TEST_DEVICE"):
    controller = ParentController()
    controller.set_path([id])
    vector = ControllerVector({i: ChildController() for i in range(2)})
    controller.add_sub_controller("ChildVector", vector)
    gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Vector")
    fastcs = FastCS(
        controller,
        [
            EpicsCATransport(
                epicsca=EpicsCAOptions(
                    aliases={
                        f"{id}:B": f"{id}:AliasB",
                        f"{id}:B_RBV": f"{id}:AliasB_RBV",
                    }
                ),
                gui=gui_options,
            )
        ],
    )
    fastcs.run(interactive=False)


if __name__ == "__main__":
    run()
