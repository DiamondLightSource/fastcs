import asyncio
import enum

import numpy as np

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Bool, Enum, Float, Int, Table, Waveform
from fastcs.launch import FastCS
from fastcs.methods import command, scan
from fastcs.transports.epics.pva import EpicsPVATransport


class FEnum(enum.Enum):
    A = 0
    B = 1
    C = "VALUES ARE ARBITRARY"
    D = 2
    E = 5


class ParentController(Controller):
    description = "some controller"
    a: AttrRW = AttrRW(Int(max=400_000, max_alarm=40_000))
    b: AttrW = AttrW(Float(min=-1, min_alarm=-0.5))

    table: AttrRW = AttrRW(
        Table([("A", np.int32), ("B", "i"), ("C", "?"), ("D", np.float64)]),
    )


class ChildController(Controller):
    fail_on_next_e = True
    c: AttrW = AttrW(Int())

    def __init__(self, description: str | None = None):
        super().__init__(description=description)

        # A getter/setter pair against an in-memory "device", doing what an
        # AttributeIO used to do. The setter clamps the requested value and
        # returns what it accepted, which becomes both the readback and the
        # setpoint; the getter seeds the setpoint when the controller connects.
        self._clamped = 5
        self.clamped = AttrRW(Int(), getter=self.get_clamped, setter=self.set_clamped)

    async def get_clamped(self) -> int:
        return self._clamped

    async def set_clamped(self, value: int) -> int:
        self._clamped = min(max(value, 0), 100)
        return self._clamped

    @command()
    async def d(self):
        print("D: RUNNING")
        await asyncio.sleep(0.1)
        print("D: FINISHED")
        await self.j.update(self.j.readback + 1)

    e: AttrR = AttrR(Bool())

    @scan(1)
    async def flip_flop(self):
        await self.e.update(not self.e.readback)

    f: AttrRW = AttrRW(Enum(FEnum))
    g: AttrRW = AttrRW(Waveform(np.int64, shape=(3,)))
    h: AttrRW = AttrRW(Waveform(np.float64, shape=(3, 3)))

    @command()
    async def i(self):
        print("I: RUNNING")
        await asyncio.sleep(0.1)
        if self.fail_on_next_e:
            self.fail_on_next_e = False
            raise RuntimeError("I: FAILED WITH THIS WEIRD ERROR")
        else:
            self.fail_on_next_e = True
            print("I: FINISHED")
            await self.j.update(self.j.readback + 1)

    j: AttrR = AttrR(Int())


def run(id="P4P_TEST_DEVICE"):
    p4p_options = EpicsPVATransport()
    controller = ParentController()
    controller.set_path([id])

    class ChildVector(ControllerVector):
        vector_attribute: AttrR = AttrR(Int())

        def __init__(self, children, description=None):
            super().__init__(children, description)

    sub_controller = ChildVector(
        {
            1: ChildController(description="some sub controller"),
            2: ChildController(description="another sub controller"),
        },
        description="some child vector",
    )

    controller.add_sub_controller("child", sub_controller)

    fastcs = FastCS(controller, [p4p_options])
    fastcs.run()


if __name__ == "__main__":
    run()
