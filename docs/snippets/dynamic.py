import json
from dataclasses import KW_ONLY, dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from fastcs.attributes import (
    Attribute,
    AttributeIO,
    AttributeIORef,
    AttrR,
    AttrRW,
    AttrW,
)
from fastcs.connections import IPConnection
from fastcs.controllers import Controller, ControllerSpec
from fastcs.datatypes import Bool, DataType, Float, Int, String
from fastcs.launch import FastCS
from fastcs.transports.epics.ca import EpicsCATransport


class TemperatureControllerParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    type: Literal["bool", "int", "float", "str"]
    access_mode: Literal["r", "rw"]

    @property
    def fastcs_datatype(self) -> DataType:
        match self.type:
            case "bool":
                return Bool()
            case "int":
                return Int()
            case "float":
                return Float()
            case "str":
                return String()


def create_attributes(parameters: dict[str, Any]) -> dict[str, Attribute]:
    attributes: dict[str, Attribute] = {}
    for name, parameter in parameters.items():
        name = name.replace(" ", "_").lower()

        try:
            parameter = TemperatureControllerParameter.model_validate(parameter)
        except ValidationError as e:
            print(f"Failed to validate parameter '{parameter}'\n{e}")
            continue

        io_ref = TemperatureControllerAttributeIORef(parameter.command)
        match parameter.access_mode:
            case "r":
                attributes[name] = AttrR(parameter.fastcs_datatype, io_ref=io_ref)
            case "rw":
                attributes[name] = AttrRW(parameter.fastcs_datatype, io_ref=io_ref)

    return attributes


class TemperatureControllerConnection(IPConnection[dict[str, Any]]):
    async def introspect(self) -> dict[str, Any]:
        response = await self.send_query("API?\r\n")
        return json.loads(response.strip("\r\n"))


NumberT = TypeVar("NumberT", int, float)


@dataclass
class TemperatureControllerAttributeIORef(AttributeIORef):
    name: str
    _: KW_ONLY
    update_period: float | None = 0.2


class TemperatureControllerAttributeIO(
    AttributeIO[NumberT, TemperatureControllerAttributeIORef]
):
    def __init__(self, connection: TemperatureControllerConnection):
        super().__init__()

        self._connection = connection

    async def update(self, attr: AttrR[NumberT, TemperatureControllerAttributeIORef]):
        query = f"{attr.io_ref.name}?"
        response = await self._connection.send_query(f"{query}\r\n")
        value = response.strip("\r\n")

        await attr.update(attr.dtype(value))

    async def send(
        self, attr: AttrW[NumberT, TemperatureControllerAttributeIORef], value: NumberT
    ) -> None:
        command = f"{attr.io_ref.name}={attr.dtype(value)}"
        await self._connection.send_command(f"{command}\r\n")


class TemperatureRampController(Controller):
    def __init__(
        self,
        index: int,
        parameters: dict[str, Any],
        io: TemperatureControllerAttributeIO,
    ):
        super().__init__(f"Ramp{index}", ios=[io])

        for name, attribute in create_attributes(parameters).items():
            self.add_attribute(name, attribute)


class TemperatureController(Controller):
    def __init__(self, connection: TemperatureControllerConnection):
        io = TemperatureControllerAttributeIO(connection)
        super().__init__(ios=[io])

        api = dict(connection.introspection)
        ramps_api = api.pop("Ramps")

        for name, attribute in create_attributes(api).items():
            self.add_attribute(name, attribute)

        for idx, ramp_parameters in enumerate(ramps_api):
            ramp_controller = TemperatureRampController(idx + 1, ramp_parameters, io)
            self.add_sub_controller(f"Ramp{idx + 1:02d}", ramp_controller)


epics_ca = EpicsCATransport()
connection = TemperatureControllerConnection("localhost", 25565)
spec = ControllerSpec("DEMO", TemperatureController, connection)
fastcs = FastCS(spec, [epics_ca])


if __name__ == "__main__":
    fastcs.run()
