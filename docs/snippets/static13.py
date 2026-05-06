import asyncio
import enum
import json
from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import Enum, Float, Int, String
from fastcs.launch import FastCS
from fastcs.methods import command, scan
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport

NumberT = TypeVar("NumberT", int, float)


@dataclass
class TemperatureControllerAttributeIORef(AttributeIORef):
    name: str
    _: KW_ONLY
    update_period: float | None = 0.2


class TemperatureControllerAttributeIO(
    AttributeIO[NumberT, TemperatureControllerAttributeIORef]
):
    def __init__(self, connection: IPConnection, suffix: str = ""):
        super().__init__()

        self._connection = connection
        self._suffix = suffix

    async def update(self, attr: AttrR[NumberT, TemperatureControllerAttributeIORef]):
        query = f"{attr.io_ref.name}{self._suffix}?"
        response = await self._connection.send_query(f"{query}\r\n")
        value = response.strip("\r\n")

        await attr.update(attr.dtype(value))

    async def send(
        self, attr: AttrW[NumberT, TemperatureControllerAttributeIORef], value: NumberT
    ) -> None:
        command = f"{attr.io_ref.name}{self._suffix}={attr.dtype(value)}"
        await self._connection.send_command(f"{command}\r\n")


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


class TemperatureRampController(Controller):
    start = AttrRW(Int(), io_ref=TemperatureControllerAttributeIORef(name="S"))
    end = AttrRW(Int(), io_ref=TemperatureControllerAttributeIORef(name="E"))
    enabled = AttrRW(Enum(OnOffEnum), io_ref=TemperatureControllerAttributeIORef("N"))
    target = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef("T"))
    actual = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef("A"))
    voltage = AttrR(Float())

    def __init__(self, index: int, connection: IPConnection) -> None:
        suffix = f"{index:02d}"
        super().__init__(
            f"Ramp{suffix}", ios=[TemperatureControllerAttributeIO(connection, suffix)]
        )


class TemperatureController(Controller):
    device_id = AttrR(String(), io_ref=TemperatureControllerAttributeIORef("ID"))
    power = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef("P"))
    ramp_rate = AttrRW(Float(), io_ref=TemperatureControllerAttributeIORef("R"))

    def __init__(self, ramp_count: int, settings: IPConnectionSettings):
        self._ip_settings = settings
        self._connection = IPConnection()

        super().__init__(ios=[TemperatureControllerAttributeIO(self._connection)])

        self._ramp_controllers: list[TemperatureRampController] = []
        for index in range(1, ramp_count + 1):
            controller = TemperatureRampController(index, self._connection)
            self._ramp_controllers.append(controller)
            self.add_sub_controller(f"R{index}", controller)

    async def connect(self):
        await self._connection.connect(self._ip_settings)

    @scan(0.1)
    async def update_voltages(self):
        voltages = json.loads(
            (await self._connection.send_query("V?\r\n")).strip("\r\n")
        )
        for index, controller in enumerate(self._ramp_controllers):
            await controller.voltage.update(float(voltages[index]))

    @command()
    async def disable_all(self) -> None:
        for rc in self._ramp_controllers:
            await rc.enabled.put(OnOffEnum.Off, sync_setpoint=True)
            # TODO: The requests all get concatenated and the sim doesn't handle it
            await asyncio.sleep(0.1)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(4, connection_settings)
controller.set_id("DEMO")
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
