import asyncio
import enum
import json
from dataclasses import KW_ONLY, dataclass
from typing import TypeVar

import numpy as np

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Enum, Float, Int, Waveform
from fastcs.logging import logger
from fastcs.methods import command, scan

NumberT = TypeVar("NumberT", int, float)


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


@dataclass
class TemperatureControllerSettings:
    num_ramp_controllers: int
    ip_settings: IPConnectionSettings


@dataclass
class TemperatureControllerAttributeIORef(AttributeIORef):
    name: str
    _: KW_ONLY
    update_period: float | None = 0.2


class TemperatureControllerAttributeIO(
    AttributeIO[NumberT, TemperatureControllerAttributeIORef]
):
    def __init__(self, connection: IPConnection, suffix: str):
        super().__init__()

        self._connection = connection
        self.suffix = suffix

    async def send(
        self, attr: AttrW[NumberT, TemperatureControllerAttributeIORef], value: NumberT
    ) -> None:
        command = f"{attr.io_ref.name}{self.suffix}={attr.dtype(value)}"
        await self._connection.send_command(f"{command}\r\n")
        self.log_event("Send command for attribute", topic=attr, command=command)

    async def update(
        self, attr: AttrR[NumberT, TemperatureControllerAttributeIORef]
    ) -> None:
        query = f"{attr.io_ref.name}{self.suffix}?"
        response = await self._connection.send_query(f"{query}\r\n")
        response = response.strip("\r\n")
        self.log_event(
            "Query for attribute",
            topic=attr,
            query=query,
            response=response,
        )

        await attr.update(attr.dtype(response))


class TemperatureController(Controller):
    ramp_rate = AttrRW(Float(), io_ref=TemperatureControllerAttributeIORef(name="R"))
    power = AttrR(Float(), io_ref=TemperatureControllerAttributeIORef(name="P"))
    voltages = AttrR(Waveform(np.int32, shape=(4,)))

    def __init__(self, settings: TemperatureControllerSettings) -> None:
        self.connection = IPConnection()
        self.suffix = ""
        super().__init__(
            ios=[TemperatureControllerAttributeIO(self.connection, self.suffix)]
        )

        self._settings = settings

        self.ramps: ControllerVector[TemperatureRampController] = ControllerVector(
            {
                index: TemperatureRampController(index, self.connection)
                for index in range(1, settings.num_ramp_controllers + 1)
            }
        )

    @command()
    async def cancel_all(self) -> None:
        for rc in self.ramps.values():
            await rc.enabled.put(OnOffEnum.Off, sync_setpoint=True)
            # TODO: The requests all get concatenated and the sim doesn't handle it
            await asyncio.sleep(0.1)

    async def connect(self) -> None:
        await self.connection.connect(self._settings.ip_settings)

    async def reconnect(self):
        try:
            await self.connection.close()
            await self.connection.connect(self._settings.ip_settings)
        except BaseException:
            logger.exception("Reconnect failed")
            return

        self._connected = True

    async def close(self) -> None:
        await self.connection.close()

    @scan(0.1)
    async def update_voltages(self):
        query = "V?"
        voltages = json.loads(
            (await self.connection.send_query(f"{query}\r\n")).strip("\r\n")
        )

        await self.voltages.update(voltages)

        for index, controller in self.ramps.items():
            self.log_event(
                "Update voltages",
                topic=controller.voltage,
                query=query,
                response=voltages,
            )
            await controller.voltage.update(float(voltages[index - 1]))


class TemperatureRampController(Controller):
    start = AttrRW(Int(), io_ref=TemperatureControllerAttributeIORef(name="S"))
    end = AttrRW(Int(), io_ref=TemperatureControllerAttributeIORef(name="E"))
    enabled = AttrRW(
        Enum(OnOffEnum), io_ref=TemperatureControllerAttributeIORef(name="N")
    )
    target = AttrR(Float(prec=3), io_ref=TemperatureControllerAttributeIORef(name="T"))
    actual = AttrR(Float(prec=3), io_ref=TemperatureControllerAttributeIORef(name="A"))
    voltage = AttrR(Float(prec=3))

    def __init__(self, index: int, conn: IPConnection) -> None:
        suffix = f"{index:02d}"
        super().__init__(
            f"Ramp{suffix}", ios=[TemperatureControllerAttributeIO(conn, suffix)]
        )
        self.connection = conn
