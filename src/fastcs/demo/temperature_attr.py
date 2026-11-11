"""Example 2 - getter/setter: per-attribute IO wired via callables in ``__init__``.

The device's protocol is written as a plain class with one ``async`` method per
command, each doing its own IO and returning a typed value - the shape a
manufacturer's own library usually already has. Those methods *are* the getters and
setters::

    self.ramp_rate = AttrRW(
        getter=Polled(protocol.get_ramp_rate, period=0.2),
        setter=protocol.set_ramp_rate,
    )

Nothing sits between the protocol and the attribute: no IO class hierarchy, no
per-attribute ref object, no adapter. Because each method annotates its types, the
datatype is inferred from them, so most attributes do not restate it - only the ones
that want metadata the annotation cannot carry, like ``Float(prec=3)``.

Because the attributes are wired in ``__init__`` rather than the class body, each one
can close over per-instance state - which is what lets a ramp's index be baked into
its protocol instead of dispatched on at IO time. This module also carries the
composition and methods rungs: a ``ControllerVector`` of ``TemperatureRampController``
sub-controllers, plus ``@scan`` and ``@command``.
"""

import asyncio
import enum
import json
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from fastcs.attributes import AttrR, AttrRW, Polled
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import DType_T, Float, Waveform
from fastcs.logging import logger
from fastcs.methods import command, scan


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


@dataclass
class TemperatureControllerSettings:
    num_ramp_controllers: int
    ip_settings: IPConnectionSettings


class TemperatureProtocol:
    """The device's wire protocol - one async method per command, doing its own IO.

    This is the layer a manufacturer would ship: it knows how to talk to the device
    and nothing about FastCS. Each method is a zero- or one-argument coroutine
    returning an annotated type, which is exactly what an attribute's ``getter`` and
    ``setter`` are, so they can be handed over as-is.
    """

    def __init__(self, connection: IPConnection, suffix: str = "") -> None:
        self._connection = connection
        self._suffix = suffix

    async def _query(self, param: str, dtype: Callable[[str], DType_T]) -> DType_T:
        query = f"{param}{self._suffix}?\r\n"
        response = (await self._connection.send_query(query)).strip("\r\n")
        logger.trace("Query for attribute", query=query, response=response)
        return dtype(response)

    async def _command(self, param: str, value: object) -> None:
        command = f"{param}{self._suffix}={value}\r\n"
        await self._connection.send_command(command)
        logger.trace("Send command for attribute", command=command)

    async def get_ramp_rate(self) -> float:
        return await self._query("R", float)

    async def set_ramp_rate(self, value: float) -> None:
        await self._command("R", value)

    async def get_power(self) -> float:
        return await self._query("P", float)

    async def get_voltages(self) -> np.ndarray:
        query = "V?\r\n"
        response = (await self._connection.send_query(query)).strip("\r\n")
        logger.trace("Query for attribute", query=query, response=response)
        return np.array(json.loads(response), dtype=np.int32)


class TemperatureRampProtocol(TemperatureProtocol):
    """The protocol of a single ramp, whose commands are suffixed by its index.

    The index is baked into the instance, so every command is still a zero- or
    one-argument callable that can be handed to an attribute as-is - no dispatching
    on which ramp is being addressed at IO time.
    """

    def __init__(self, connection: IPConnection, index: int) -> None:
        super().__init__(connection, suffix=f"{index:02d}")

    async def get_start(self) -> int:
        return await self._query("S", int)

    async def set_start(self, value: int) -> None:
        await self._command("S", value)

    async def get_end(self) -> int:
        return await self._query("E", int)

    async def set_end(self, value: int) -> None:
        await self._command("E", value)

    async def get_enabled(self) -> OnOffEnum:
        return await self._query("N", OnOffEnum)

    async def set_enabled(self, value: OnOffEnum) -> None:
        await self._command("N", value)

    async def get_target(self) -> float:
        return await self._query("T", float)

    async def get_actual(self) -> float:
        return await self._query("A", float)


class TemperatureController(Controller):
    def __init__(self, settings: TemperatureControllerSettings) -> None:
        self.connection = IPConnection()
        self._settings = settings
        self._protocol = TemperatureProtocol(self.connection)

        super().__init__()

        # No datatype: inferred from get_ramp_rate's `-> float` annotation.
        self.ramp_rate = AttrRW(
            getter=Polled(self._protocol.get_ramp_rate, period=0.2),
            setter=self._protocol.set_ramp_rate,
        )
        self.power = AttrR(getter=Polled(self._protocol.get_power, period=0.2))
        # Updated by the update_voltages scan below, so no IO of its own
        self.voltages = AttrR(Waveform(np.int32, shape=(4,)))

        self.ramps = ControllerVector(
            {
                index: TemperatureRampController(index, self.connection)
                for index in range(1, settings.num_ramp_controllers + 1)
            }
        )

    @command()
    async def cancel_all(self) -> None:
        for rc in self.ramps.values():
            await rc.enabled.set(OnOffEnum.Off)
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
        voltages = await self._protocol.get_voltages()

        await self.voltages.update(voltages)

        for index, controller in self.ramps.items():
            self.log_event(
                "Update voltages", topic=controller.voltage, response=voltages
            )
            await controller.voltage.update(float(voltages[index - 1]))


class TemperatureRampController(Controller):
    def __init__(self, index: int, conn: IPConnection) -> None:
        self._protocol = TemperatureRampProtocol(conn, index)

        super().__init__(f"Ramp{index:02d}")

        self.connection = conn

        # Datatypes inferred from the protocol methods' annotations - including the
        # enum, whose members come from OnOffEnum via get_enabled's return type.
        self.start = AttrRW(
            getter=Polled(self._protocol.get_start, period=0.2),
            setter=self._protocol.set_start,
        )
        self.end = AttrRW(
            getter=Polled(self._protocol.get_end, period=0.2),
            setter=self._protocol.set_end,
        )
        self.enabled = AttrRW(
            getter=Polled(self._protocol.get_enabled, period=0.2),
            setter=self._protocol.set_enabled,
        )
        # Stated explicitly, to carry metadata the annotation cannot: `-> float`
        # says nothing about display precision.
        self.target = AttrR(
            Float(prec=3), getter=Polled(self._protocol.get_target, period=0.2)
        )
        self.actual = AttrR(
            Float(prec=3), getter=Polled(self._protocol.get_actual, period=0.2)
        )
        # Updated by the parent controller's update_voltages scan
        self.voltage = AttrR(Float(prec=3))
