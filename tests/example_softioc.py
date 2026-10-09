import asyncio
from enum import IntEnum
from pathlib import Path

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Enum, Int
from fastcs.methods import command
from fastcs.transports.epics.ca.transport import (
    EpicsCAOptions,
    EpicsCATransport,
    EpicsGUIOptions,
)
from fastcs.transports.epics.options import EnumMapping


class ExampleEnum(IntEnum):
    Invalid = 0
    Idle = 1
    Active = 2


class ParentController(Controller):
    a: AttrR = AttrR(Int())
    b: AttrRW = AttrRW(Int())


class ChildController(Controller):
    fail_on_next_e: bool = True
    c: AttrW = AttrW(Int())

    @command()
    async def d(self):
        print("D: RUNNING")
        await asyncio.sleep(0)
        if self.fail_on_next_e:
            self.fail_on_next_e = False
            raise RuntimeError("D: FAILED WITH THIS WEIRD ERROR")
        else:
            self.fail_on_next_e = True
            print("D: FINISHED")

    e: AttrRW = AttrRW(Enum(ExampleEnum))


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
                        f"{id}:ChildVector:0:E": EnumMapping(
                            pv=f"{id}:EnumAliasE", mapping={"Off": 1, "On": 2}
                        ),
                        f"{id}:ChildVector:0:E_RBV": EnumMapping(
                            pv=f"{id}:EnumAliasE_RBV", mapping={"Off": 1, "On": 2}
                        ),
                        f"{id}:ChildVector:0:D": EnumMapping(
                            pv=f"{id}:EnumAliasD",
                            mapping={"Idle": False, "Active": True},
                        ),
                    }
                ),
                gui=gui_options,
            )
        ],
    )
    fastcs.run(interactive=False)


if __name__ == "__main__":
    run()
