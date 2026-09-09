"""Example 4 - declarative: a SCPI device's attributes written as annotated hints.

The same multi-ramp temperature controller as :mod:`fastcs.demo.temperature_attr`, and
the same device behind it, declared rather than constructed. Every attribute is a
class-body hint carrying a `SCPIParam` that says both what to send and what the
value means::

    ramp_rate: Annotated[AttrRW[float], SCPIParam("R", precision=3, units="K/s")]

Contrast with the two examples either side of it:

- :mod:`fastcs.demo.temperature_attr` wires the same device **procedurally** - each
  getter and setter handed to a constructor in ``__init__``. Use that when the
  attributes depend on data only ``__init__`` has.
- :mod:`fastcs.demo.eiger` builds its attributes from what the device **reports**. Use
  that when the device describes itself.

A SCPI device does neither: it does not describe itself, which is exactly why its
attributes are hand-annotated, and the metadata it cannot report - precision, units,
a human-readable description - is written where the attribute is declared.

This module also carries the composition and methods rungs: a `ControllerVector` of
ramp sub controllers, a ``@scan`` deriving values the device reports in one go, and
a ``@command`` acting across the whole vector.
"""

import asyncio
import enum
import json
from dataclasses import dataclass
from typing import Annotated

import numpy as np

from fastcs.attributes import AttrR, AttrRW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import ControllerVector
from fastcs.datatypes import Array1D
from fastcs.demo.scpi import SCPIController, SCPIParam
from fastcs.logging import logger
from fastcs.methods import command, scan


class OnOffEnum(enum.StrEnum):
    Off = "0"
    On = "1"


@dataclass
class TemperatureControllerSettings:
    num_ramp_controllers: int
    ip_settings: IPConnectionSettings


class TemperatureRampController(SCPIController):
    """One ramp, addressed by a two-digit suffix on every command token.

    The suffix is the whole of what distinguishes ramp 1 from ramp 2, so the
    declarations below are written once and serve every ramp.
    """

    start: Annotated[
        AttrRW[int],
        SCPIParam("S", description="Temperature the ramp starts from"),
    ]
    end: Annotated[
        AttrRW[int],
        SCPIParam("E", description="Temperature the ramp finishes at"),
    ]
    enabled: Annotated[
        AttrRW[OnOffEnum],
        SCPIParam("N", description="Whether this ramp is running"),
    ]
    # `precision` is the kind of thing a return annotation cannot carry: `-> float`
    # says nothing about how many decimal places are meaningful.
    target: Annotated[
        AttrR[float],
        SCPIParam("T", precision=3, units="degC", description="Where the ramp is at"),
    ]
    actual: Annotated[
        AttrR[float],
        SCPIParam("A", precision=3, units="degC", description="Measured temperature"),
    ]
    # Updated by the parent's `update_voltages` scan, which reads every ramp's
    # voltage in one query, so this one has no command token of its own.
    voltage: AttrR[float]

    def __init__(self, index: int, connection: IPConnection) -> None:
        super().__init__(
            connection, suffix=f"{index:02d}", description=f"Ramp {index}"
        )

    async def initialise(self) -> None:
        await super().initialise()

        # No `SCPIParam`, so `SCPIController` left it alone: filled here with the
        # metadata it wants and no IO, because the parent updates it.
        self.filler.fill_attribute("voltage", precision=3, units="V")


class TemperatureController(SCPIController):
    """The whole device: its own parameters, plus a ramp sub controller each."""

    ramp_rate: Annotated[
        AttrRW[float],
        SCPIParam("R", precision=3, units="K/s", description="Rate of change"),
    ]
    power: Annotated[
        AttrR[float],
        SCPIParam("P", precision=3, units="W", description="Power draw"),
    ]
    # Read by the scan below in a single query rather than one per ramp, so it
    # carries no command token either.
    voltages: AttrR[Array1D[np.int32]]

    ramps: ControllerVector[TemperatureRampController]

    def __init__(self, settings: TemperatureControllerSettings) -> None:
        self._settings = settings

        super().__init__(IPConnection())

        self.ramps = ControllerVector(
            {
                index: TemperatureRampController(index, self.connection)
                for index in range(1, settings.num_ramp_controllers + 1)
            }
        )

    async def initialise(self) -> None:
        await super().initialise()

        self.filler.fill_attribute("voltages", shape=(len(self.ramps),))

    async def connect(self) -> None:
        await self.connection.connect(self._settings.ip_settings)

    async def reconnect(self) -> None:
        try:
            await self.connection.close()
            await self.connection.connect(self._settings.ip_settings)
        except BaseException:
            logger.exception("Reconnect failed")
            return

        self._connected = True

    async def close(self) -> None:
        await self.connection.close()

    @command()
    async def cancel_all(self) -> None:
        for ramp in self.ramps.values():
            await ramp.enabled.set(OnOffEnum.Off)
            # TODO: The requests all get concatenated and the sim doesn't handle it
            await asyncio.sleep(0.1)

    @scan(0.1)
    async def update_voltages(self) -> None:
        """One query for every ramp's voltage, fanned out to each of them.

        The device reports them together, so reading them together is one round
        trip rather than one per ramp - which is why `voltage` is declared without
        a command token of its own.
        """
        response = (await self.connection.send_query("V?\r\n")).strip("\r\n")
        voltages = np.array(json.loads(response), dtype=np.int32)

        await self.voltages.update(voltages)

        for index, ramp in self.ramps.items():
            self.log_event("Update voltages", topic=ramp.voltage, response=voltages)
            await ramp.voltage.update(float(voltages[index - 1]))
