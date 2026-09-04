import json
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from fastcs.attributes import Attribute, AttrR, AttrRW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import DType
from fastcs.launch import FastCS
from fastcs.transports.epics.ca import EpicsCATransport

ValueT = TypeVar("ValueT")


class TemperatureProtocol:
    def __init__(self, connection: IPConnection):
        self._connection = connection

    async def send_command(self, param: str, value: ValueT, dtype: type[ValueT]):
        command = f"{param}={dtype(value)}"  # type: ignore[call-arg]
        await self._connection.send_command(f"{command}\r\n")

    async def send_query(self, param: str, dtype: type[ValueT]) -> ValueT:
        query = f"{param}?"
        response = await self._connection.send_query(f"{query}\r\n")
        return dtype(response.strip("\r\n"))  # type: ignore[call-arg]


class TemperatureControllerParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    type: Literal["bool", "int", "float", "str"]
    access_mode: Literal["r", "rw"]

    @property
    def fastcs_datatype(self) -> type[DType]:
        match self.type:
            case "bool":
                return bool
            case "int":
                return int
            case "float":
                return float
            case "str":
                return str


def create_attributes(
    parameters: dict[str, Any], protocol: TemperatureProtocol
) -> dict[str, Attribute]:
    attributes: dict[str, Attribute] = {}
    for name, parameter in parameters.items():
        name = name.replace(" ", "_").lower()

        try:
            parameter = TemperatureControllerParameter.model_validate(parameter)
        except ValidationError as e:
            print(f"Failed to validate parameter '{parameter}'\n{e}")
            continue

        datatype = parameter.fastcs_datatype
        command = parameter.command

        async def getter(command=command, dtype=datatype):
            return await protocol.send_query(command, dtype)

        match parameter.access_mode:
            case "r":
                attributes[name] = AttrR(datatype, getter=getter)
            case "rw":

                async def setter(value, command=command, dtype=datatype):
                    await protocol.send_command(command, value, dtype)

                attributes[name] = AttrRW(datatype, getter=getter, setter=setter)

    return attributes


class TemperatureRampController(Controller):
    def __init__(
        self,
        index: int,
        parameters: dict[str, TemperatureControllerParameter],
        protocol: TemperatureProtocol,
    ):
        self._parameters = parameters
        self._protocol = protocol
        super().__init__(f"Ramp{index}")

    async def build(self):
        for name, attribute in create_attributes(
            self._parameters, self._protocol
        ).items():
            self.add_attribute(name, attribute)


class TemperatureController(Controller):
    connection: IPConnection

    def __init__(self, settings: IPConnectionSettings):
        # Opening it, and reopening it after a failure, is the runner's job.
        self.connection = IPConnection(settings)
        self._protocol = TemperatureProtocol(self.connection)

        super().__init__()

    async def build(self):
        # Runs with the connection already open. The ramp controllers added here get
        # their own `build` called by the runner on a later pass, so there is no
        # need - and no way - to drive their lifecycle from this one.
        api = json.loads((await self.connection.send_query("API?\r\n")).strip("\r\n"))

        ramps_api = api.pop("Ramps")

        for name, attribute in create_attributes(api, self._protocol).items():
            self.add_attribute(name, attribute)

        for idx, ramp_parameters in enumerate(ramps_api):
            ramp_controller = TemperatureRampController(
                idx + 1, ramp_parameters, self._protocol
            )
            self.add_sub_controller(f"Ramp{idx + 1:02d}", ramp_controller)


epics_ca = EpicsCATransport()
connection_settings = IPConnectionSettings("localhost", 25565)
controller = TemperatureController(connection_settings)
controller.set_path(["DEMO"])
fastcs = FastCS(controller, [epics_ca])


if __name__ == "__main__":
    fastcs.run()
