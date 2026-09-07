from pathlib import Path

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.control_system import FastCS
from fastcs.controllers import Controller, ControllerVector
from fastcs.methods import command
from fastcs.transports.epics.ca.transport import (
    EpicsCAOptions,
    EpicsCATransport,
    EpicsGUIOptions,
)


class ParentController(Controller):
    a: AttrR[int]
    b: AttrRW[int]

    def __init__(self, description: str | None = None) -> None:
        super().__init__(description)
        self._clamped = 5
        self.clamped = AttrRW(int, getter=self.get_clamped, setter=self.set_clamped)

    async def get_clamped(self) -> int:
        return self._clamped

    async def set_clamped(self, value: int) -> int:
        self._clamped = min(max(value, 0), 100)
        return self._clamped


class ChildController(Controller):
    c: AttrW[int]

    @command()
    async def d(self):
        pass


def run(id="SOFTIOC_TEST_DEVICE"):
    controller = ParentController()
    controller.set_path([id])
    vector = ControllerVector({i: ChildController() for i in range(2)})
    controller.add_sub_controller("ChildVector", vector)
    gui_options = EpicsGUIOptions(output_dir=Path("./opis"), title="Demo Vector")
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
