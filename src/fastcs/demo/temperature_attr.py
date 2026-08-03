"""Example 2 - getter/setter: per-attribute IO wired via callables in ``__init__``.

Baseline against the CURRENT callback-IO API. A **single** generic IO class
(``TemperatureIO``) drives every attribute; the per-attribute behaviour lives in
each attribute's ``TemperatureIORef``, which just carries the command-building
callables (``read_cmd``/``write_cmd``) taken from a protocol class with one method
per device command. This is the honest precursor to the
``AttrRW(getter=..., setter=...)`` constructor params landing in #392:
``read_cmd``/``write_cmd`` *are* the getter/setter, and #392 simply promotes them
onto the constructor and deletes this IO/ref wrapper, while the protocol classes
survive unchanged.

Because the attributes are wired in ``__init__`` rather than the class body, each
one can close over per-instance state - which is what lets a ramp's index be baked
into its protocol instead of dispatched on at IO time. This module also carries the
composition and methods rungs: a ``ControllerVector`` of ``TemperatureRampController``
sub-controllers, plus ``@scan`` and ``@command``.
"""

import asyncio
import enum
import json
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import Any, TypeVar

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


class TemperatureProtocol:
    """The device wire protocol - one method per command, referenced by the IORefs.

    Each getter returns the query string to send; each setter returns the command
    string to send for a given value. These are exactly the callables #392 will pass
    straight to ``AttrRW(getter=..., setter=...)``.
    """

    def get_ramp_rate(self) -> str:
        return "R?\r\n"

    def set_ramp_rate(self, value: float) -> str:
        return f"R={value}\r\n"

    def get_power(self) -> str:
        return "P?\r\n"

    def get_voltages(self) -> str:
        return "V?\r\n"


class TemperatureRampProtocol:
    """The wire protocol of a single ramp, whose commands are suffixed by its index.

    The index is baked into the instance, so every command is still a zero- or
    one-argument callable that can be handed to an attribute as-is.
    """

    def __init__(self, index: int) -> None:
        self.suffix = f"{index:02d}"

    def get_start(self) -> str:
        return f"S{self.suffix}?\r\n"

    def set_start(self, value: int) -> str:
        return f"S{self.suffix}={value}\r\n"

    def get_end(self) -> str:
        return f"E{self.suffix}?\r\n"

    def set_end(self, value: int) -> str:
        return f"E{self.suffix}={value}\r\n"

    def get_enabled(self) -> str:
        return f"N{self.suffix}?\r\n"

    def set_enabled(self, value: OnOffEnum) -> str:
        return f"N{self.suffix}={value}\r\n"

    def get_target(self) -> str:
        return f"T{self.suffix}?\r\n"

    def get_actual(self) -> str:
        return f"A{self.suffix}?\r\n"


@dataclass
class TemperatureIORef(AttributeIORef):
    """Per-attribute IO spec: the command-building callables for one attribute."""

    read_cmd: Callable[[], str]
    write_cmd: Callable[[Any], str] | None = None
    _: KW_ONLY
    update_period: float | None = 0.2


class TemperatureIO(AttributeIO[NumberT, TemperatureIORef]):
    """A single generic IO shared by every attribute; behaviour comes from the ref."""

    def __init__(self, connection: IPConnection):
        super().__init__()

        self._connection = connection

    async def update(self, attr: AttrR[NumberT, TemperatureIORef]) -> None:
        query = attr.io_ref.read_cmd()
        response = (await self._connection.send_query(query)).strip("\r\n")
        self.log_event(
            "Query for attribute",
            topic=attr,
            query=query,
            response=response,
        )

        await attr.update(attr.dtype(response))

    async def send(
        self, attr: AttrW[NumberT, TemperatureIORef], value: NumberT
    ) -> None:
        if attr.io_ref.write_cmd is None:
            raise TypeError(f"{attr} is read-only: no write_cmd on its io_ref")

        command = attr.io_ref.write_cmd(value)
        await self._connection.send_command(command)
        self.log_event("Send command for attribute", topic=attr, command=command)


class TemperatureController(Controller):
    def __init__(self, settings: TemperatureControllerSettings) -> None:
        self.connection = IPConnection()
        self._settings = settings
        self._protocol = TemperatureProtocol()

        super().__init__(ios=[TemperatureIO(self.connection)])

        self.ramp_rate = AttrRW(
            Float(),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_ramp_rate,
                write_cmd=self._protocol.set_ramp_rate,
            ),
        )
        self.power = AttrR(
            Float(), io_ref=TemperatureIORef(read_cmd=self._protocol.get_power)
        )
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
        query = self._protocol.get_voltages()
        voltages = json.loads((await self.connection.send_query(query)).strip("\r\n"))

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
    def __init__(self, index: int, conn: IPConnection) -> None:
        self._protocol = TemperatureRampProtocol(index)

        super().__init__(f"Ramp{self._protocol.suffix}", ios=[TemperatureIO(conn)])

        self.connection = conn

        self.start = AttrRW(
            Int(),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_start,
                write_cmd=self._protocol.set_start,
            ),
        )
        self.end = AttrRW(
            Int(),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_end,
                write_cmd=self._protocol.set_end,
            ),
        )
        self.enabled = AttrRW(
            Enum(OnOffEnum),
            io_ref=TemperatureIORef(
                read_cmd=self._protocol.get_enabled,
                write_cmd=self._protocol.set_enabled,
            ),
        )
        self.target = AttrR(
            Float(prec=3),
            io_ref=TemperatureIORef(read_cmd=self._protocol.get_target),
        )
        self.actual = AttrR(
            Float(prec=3),
            io_ref=TemperatureIORef(read_cmd=self._protocol.get_actual),
        )
        # Updated by the parent controller's update_voltages scan
        self.voltage = AttrR(Float(prec=3))
