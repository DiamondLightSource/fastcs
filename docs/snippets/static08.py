from pathlib import Path
from typing import TypeVar

from fastcs.attributes import AttrR, Polled
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


class TemperatureController(Controller):
    def __init__(self, settings: IPConnectionSettings):
        self._ip_settings = settings
        self._connection = IPConnection()
        self._protocol = TemperatureProtocol(self._connection)

        super().__init__()

        self.device_id = AttrR(str, getter=Polled(self._get_device_id, period=0.2))
        self.power = AttrR(float, getter=Polled(self._get_power, period=0.2))

    async def _get_device_id(self) -> str:
        return await self._protocol.send_query("ID", str)

    async def _get_power(self) -> float:
        return await self._protocol.send_query("P", float)

    async def connect(self):
        await self._connection.connect(self._ip_settings)


gui_options = EpicsGUIOptions(output_dir=Path("."), title="Demo Temperature Controller")
epics_ca = EpicsCATransport(gui=gui_options)
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(connection_settings)
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])

if __name__ == "__main__":
    fastcs.run()
