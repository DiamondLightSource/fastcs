import asyncio
import enum
import json
from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttrR, AttrRW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import Enum, Float, Int, String
from fastcs.launch import FastCS
from fastcs.logging import LogLevel, configure_logging, logger
from fastcs.methods import command, scan
from fastcs.tracer import Tracer
from fastcs.transports.epics import EpicsGUIOptions
from fastcs.transports.epics.ca import EpicsCATransport

ValueT = TypeVar("ValueT")


class TemperatureProtocol(Tracer):
    def __init__(self, connection: IPConnection, suffix: str = ""):
        super().__init__()
        self._connection = connection
        self._suffix = suffix

    async def send_command(self, param: str, value: ValueT, dtype: type[ValueT]):
        command = f"{param}{self._suffix}={dtype(value)}"  # type: ignore[call-arg]

        logger.info("Sending attribute value", command=command)

        await self._connection.send_command(f"{command}\r\n")

    async def send_query(
        self, param: str, dtype: type[ValueT], topic: Tracer | None = None
    ) -> ValueT:
        query = f"{param}{self._suffix}?"
        response = await self._connection.send_query(f"{query}\r\n")
        value = dtype(response.strip("\r\n"))  # type: ignore[call-arg]

        self.log_event("Query for attribute", topic=topic, query=query, response=value)

        return value


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


class TemperatureRampController(Controller):
    def __init__(self, index: int, connection: IPConnection) -> None:
        suffix = f"{index:02d}"
        self._protocol = TemperatureProtocol(connection, suffix)
        super().__init__(f"Ramp{suffix}")

        self.start = AttrRW(
            Int(), getter=self._get_start, setter=self._set_start, poll_period=0.2
        )
        self.end = AttrRW(
            Int(), getter=self._get_end, setter=self._set_end, poll_period=0.2
        )
        self.enabled = AttrRW(
            Enum(OnOffEnum),
            getter=self._get_enabled,
            setter=self._set_enabled,
            poll_period=0.2,
        )
        self.target = AttrR(Float(), getter=self._get_target, poll_period=0.2)
        self.actual = AttrR(Float(), getter=self._get_actual, poll_period=0.2)
        self.voltage = AttrR(Float())

    async def _get_start(self) -> int:
        return await self._protocol.send_query("S", int, topic=self.start)

    async def _set_start(self, value: int) -> None:
        await self._protocol.send_command("S", value, int)

    async def _get_end(self) -> int:
        return await self._protocol.send_query("E", int, topic=self.end)

    async def _set_end(self, value: int) -> None:
        await self._protocol.send_command("E", value, int)

    async def _get_enabled(self) -> OnOffEnum:
        return OnOffEnum(await self._protocol.send_query("N", str, topic=self.enabled))

    async def _set_enabled(self, value: OnOffEnum) -> None:
        await self._protocol.send_command("N", value.value, str)

    async def _get_target(self) -> float:
        return await self._protocol.send_query("T", float, topic=self.target)

    async def _get_actual(self) -> float:
        return await self._protocol.send_query("A", float, topic=self.actual)


class TemperatureController(Controller):
    def __init__(self, ramp_count: int, settings: IPConnectionSettings):
        self._ip_settings = settings
        self._connection = IPConnection()
        self._protocol = TemperatureProtocol(self._connection)

        super().__init__()

        self.device_id = AttrR(String(), getter=self._get_device_id, poll_period=0.2)
        self.power = AttrR(Float(), getter=self._get_power, poll_period=0.2)
        self.ramp_rate = AttrRW(
            Float(),
            getter=self._get_ramp_rate,
            setter=self._set_ramp_rate,
            poll_period=0.2,
        )

        self._ramp_controllers: list[TemperatureRampController] = []
        for index in range(1, ramp_count + 1):
            controller = TemperatureRampController(index, self._connection)
            self._ramp_controllers.append(controller)
            self.add_sub_controller(f"R{index}", controller)

    async def _get_device_id(self) -> str:
        return await self._protocol.send_query("ID", str, topic=self.device_id)

    async def _get_power(self) -> float:
        return await self._protocol.send_query("P", float, topic=self.power)

    async def _get_ramp_rate(self) -> float:
        return await self._protocol.send_query("R", float, topic=self.ramp_rate)

    async def _set_ramp_rate(self, value: float) -> None:
        await self._protocol.send_command("R", value, float)

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
        self.log_event("Disabling all ramps")
        for rc in self._ramp_controllers:
            await rc.enabled.set(OnOffEnum.Off)
            # TODO: The requests all get concatenated and the sim doesn't handle it
            await asyncio.sleep(0.1)


configure_logging(LogLevel.TRACE)

gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
logger.info("Configuring connection settings", connection_settings=connection_settings)
controller = TemperatureController(4, connection_settings)
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
