import enum
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttrR, AttrRW, Polled
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport

ValueT = TypeVar("ValueT")


class TemperatureProtocol:
    def __init__(self, connection: IPConnection, suffix: str = ""):
        self._connection = connection
        self._suffix = suffix

    async def send_command(self, param: str, value: ValueT, dtype: type[ValueT]):
        command = f"{param}{self._suffix}={dtype(value)}"  # type: ignore[call-arg]
        await self._connection.send_command(f"{command}\r\n")

    async def send_query(self, param: str, dtype: type[ValueT]) -> ValueT:
        query = f"{param}{self._suffix}?"
        response = await self._connection.send_query(f"{query}\r\n")
        return dtype(response.strip("\r\n"))  # type: ignore[call-arg]


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


class TemperatureRampController(Controller):
    def __init__(self, index: int, connection: IPConnection) -> None:
        suffix = f"{index:02d}"
        self._protocol = TemperatureProtocol(connection, suffix)
        super().__init__(f"Ramp{suffix}")

        self.start = AttrRW(
            int, getter=Polled(self._get_start, period=0.2), setter=self._set_start
        )
        self.end = AttrRW(
            int, getter=Polled(self._get_end, period=0.2), setter=self._set_end
        )
        self.enabled = AttrRW(
            OnOffEnum,
            getter=Polled(self._get_enabled, period=0.2),
            setter=self._set_enabled,
        )

    async def _get_start(self) -> int:
        return await self._protocol.send_query("S", int)

    async def _set_start(self, value: int) -> None:
        await self._protocol.send_command("S", value, int)

    async def _get_end(self) -> int:
        return await self._protocol.send_query("E", int)

    async def _set_end(self, value: int) -> None:
        await self._protocol.send_command("E", value, int)

    async def _get_enabled(self) -> OnOffEnum:
        return OnOffEnum(await self._protocol.send_query("N", str))

    async def _set_enabled(self, value: OnOffEnum) -> None:
        await self._protocol.send_command("N", value.value, str)


class TemperatureController(Controller):
    def __init__(self, ramp_count: int, settings: IPConnectionSettings):
        self._ip_settings = settings
        self._connection = IPConnection()
        self._protocol = TemperatureProtocol(self._connection)

        super().__init__()

        self.device_id = AttrR(str, getter=Polled(self._get_device_id, period=0.2))
        self.power = AttrR(float, getter=Polled(self._get_power, period=0.2))
        self.ramp_rate = AttrRW(
            float,
            getter=Polled(self._get_ramp_rate, period=0.2),
            setter=self._set_ramp_rate,
        )

        self._ramp_controllers: list[TemperatureRampController] = []
        for index in range(1, ramp_count + 1):
            controller = TemperatureRampController(index, self._connection)
            self._ramp_controllers.append(controller)
            self.add_sub_controller(f"R{index}", controller)

    async def _get_device_id(self) -> str:
        return await self._protocol.send_query("ID", str)

    async def _get_power(self) -> float:
        return await self._protocol.send_query("P", float)

    async def _get_ramp_rate(self) -> float:
        return await self._protocol.send_query("R", float)

    async def _set_ramp_rate(self, value: float) -> None:
        await self._protocol.send_command("R", value, float)

    async def connect(self):
        await self._connection.connect(self._ip_settings)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(4, connection_settings)
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
