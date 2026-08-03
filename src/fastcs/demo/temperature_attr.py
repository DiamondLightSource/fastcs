"""Example 2 - getter/setter: per-attribute IO wired via callables in ``__init__``.

Each attribute carries its own IO as a pair of callables passed straight to the
constructor - ``AttrRW(getter=..., setter=...)``. The callables are built from a
protocol class with one method per device command, bound to a connection by
``TemperatureLink``; there is no IO class hierarchy and no per-attribute ref object,
just closures. Reads that should happen on a schedule say so by wrapping the getter
in ``Polled``.

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
from dataclasses import dataclass

import numpy as np

from fastcs.attributes import AttrR, AttrRW, Getter, Polled, Setter
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import DType_T, Enum, Float, Int, Waveform
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
    """The device wire protocol - one method per command.

    Each getter returns the query string to send; each setter returns the command
    string to send for a given value. ``TemperatureLink`` binds these to a connection
    to make the callables passed to ``AttrRW(getter=..., setter=...)``.
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


class TemperatureLink:
    """Turns the protocol's command builders into attribute getters and setters.

    The protocol classes above only know how to spell a command; this binds them to
    a connection so each one becomes a zero- or one-argument coroutine that an
    attribute can call directly.
    """

    def __init__(self, connection: IPConnection):
        self._connection = connection

    def getter(
        self, read_cmd: Callable[[], str], dtype: Callable[[str], DType_T]
    ) -> Getter[DType_T]:
        async def get() -> DType_T:
            query = read_cmd()
            response = (await self._connection.send_query(query)).strip("\r\n")
            logger.trace("Query for attribute", query=query, response=response)
            return dtype(response)

        return get

    def polled(
        self,
        read_cmd: Callable[[], str],
        dtype: Callable[[str], DType_T],
        period: float = 0.2,
    ) -> Polled[DType_T]:
        """A getter for ``read_cmd``, read every ``period`` seconds."""
        return Polled(self.getter(read_cmd, dtype), period=period)

    def setter(self, write_cmd: Callable[[DType_T], str]) -> Setter[DType_T]:
        async def send(value: DType_T) -> None:
            command = write_cmd(value)
            await self._connection.send_command(command)
            logger.trace("Send command for attribute", command=command)

        return send


class TemperatureController(Controller):
    def __init__(self, settings: TemperatureControllerSettings) -> None:
        self.connection = IPConnection()
        self._settings = settings
        self._protocol = TemperatureProtocol()
        link = TemperatureLink(self.connection)

        super().__init__()

        self.ramp_rate = AttrRW(
            Float(),
            getter=link.polled(self._protocol.get_ramp_rate, float),
            setter=link.setter(self._protocol.set_ramp_rate),
        )
        self.power = AttrR(Float(), getter=link.polled(self._protocol.get_power, float))
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
        link = TemperatureLink(conn)

        super().__init__(f"Ramp{self._protocol.suffix}")

        self.connection = conn

        self.start = AttrRW(
            Int(),
            getter=link.polled(self._protocol.get_start, int),
            setter=link.setter(self._protocol.set_start),
        )
        self.end = AttrRW(
            Int(),
            getter=link.polled(self._protocol.get_end, int),
            setter=link.setter(self._protocol.set_end),
        )
        self.enabled = AttrRW(
            Enum(OnOffEnum),
            getter=link.polled(self._protocol.get_enabled, OnOffEnum),
            setter=link.setter(self._protocol.set_enabled),
        )
        self.target = AttrR(
            Float(prec=3),
            getter=link.polled(self._protocol.get_target, float),
        )
        self.actual = AttrR(
            Float(prec=3),
            getter=link.polled(self._protocol.get_actual, float),
        )
        # Updated by the parent controller's update_voltages scan
        self.voltage = AttrR(Float(prec=3))
