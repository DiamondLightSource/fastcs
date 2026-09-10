"""Example 5 - dynamic controller: a cut-down Eiger over the fake REST sim.

Half the attributes (``count_time``, ``state``) are declared as type hints and
checked by the filler; the rest of the parameter tree is discovered by walking the
sim's ``keys`` endpoints in ``build`` and is added dynamically, with no static
check. A device that describes itself over the wire is exactly the case where
asking earns its complexity - contrast with the (deliberately self-describing-free)
SCPI/temperature examples.

The walk happens in `EigerDetector.build`, which the framework calls once the
connection is open. `EigerConnection` is a plain `HTTPConnection` subclass: it knows
how to talk to the detector, and nothing about what the detector turns out to have.
"""

import enum
from typing import Any, NamedTuple, cast

import httpx

from fastcs.attributes import AttrR, AttrRW, Polled
from fastcs.connections import Connections, HTTPConnection, HTTPConnectionSettings
from fastcs.controllers import Controller
from fastcs.datatypes import DType
from fastcs.demo.simulation.eiger import API_PREFIX, Subsystem, ValueType

_DATATYPES: dict[ValueType, type[DType]] = {
    "float": float,
    "int": int,
    "string": str,
    "bool": bool,
}

# Poll period (seconds) for read-only status params that change on the device.
UPDATE_PERIOD = 0.2

SUBSYSTEMS: tuple[Subsystem, ...] = ("config", "status")


class ParameterInfo(NamedTuple):
    """What the device says about one of its parameters.

    Deliberately the *shape* of the parameter and not its value: the shape is what
    ``build`` turns into an attribute, and the value changes every time it is read.
    """

    subsystem: Subsystem
    name: str
    value_type: ValueType
    access_mode: str
    allowed_values: tuple[str, ...] | None


def _datatype(info: ParameterInfo) -> type[DType]:
    """Build a datatype for a parameter from the metadata the device reports.

    A parameter that reports ``allowed_values`` is discrete, so it becomes an enum
    class built from those values. The members are only knowable over the wire,
    which is exactly the case a runtime walk exists for.
    """
    if info.allowed_values is None:
        return _DATATYPES[info.value_type]

    name = "".join(part.title() for part in info.name.split("_"))
    # The functional API builds a class; type checkers only see the instance signature.
    return cast(
        type[enum.Enum],
        enum.Enum(name, {value: value for value in info.allowed_values}),
    )


class EigerConnection(HTTPConnection):
    """HTTP to the Eiger REST sim, and the one thing that knows when it is down.

    Everything about being an HTTP connection - the client, the disconnect on a
    transport failure, the reconnect budget - comes from `HTTPConnection`. What is
    here is only what is Eiger's rather than HTTP's: the URL layout, and the
    ``{"value": ...}`` envelope the detector wraps every parameter in.

    A ``transport`` can be supplied to point directly at an in-process ASGI app
    (e.g. in tests), bypassing the network entirely.

    Args:
        settings: Where the detector's REST API lives
        transport: Optional httpx transport, for talking to an in-process app
        kwargs: Passed to `HTTPConnection`

    """

    def __init__(
        self,
        settings: HTTPConnectionSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs,
    ) -> None:
        super().__init__(settings or HTTPConnectionSettings(port=8000), **kwargs)
        self._transport = transport

    async def get(self, path: str) -> Any:
        """The detector wraps every parameter as ``{"value": ...}`` - unwrap it."""
        return (await super().get(path))["value"]

    async def keys(self, subsystem: Subsystem) -> list[str]:
        """The parameter names one subsystem reports."""
        # The listing endpoint answers with a bare list rather than an envelope,
        # so it goes through the un-overridden `get`.
        return await super().get(f"{API_PREFIX}/{subsystem}/keys")

    async def describe(self, subsystem: Subsystem, param: str) -> dict:
        """The whole envelope for one parameter: its value *and* its metadata."""
        return await super().get(f"{API_PREFIX}/{subsystem}/{param}")

    async def get_parameter(self, subsystem: Subsystem, param: str) -> Any:
        return await self.get(f"{API_PREFIX}/{subsystem}/{param}")

    async def put_parameter(self, subsystem: Subsystem, param: str, value: Any) -> None:
        await self.put(f"{API_PREFIX}/{subsystem}/{param}", {"value": value})


class EigerDetector(Controller):
    """Cut-down Eiger controller: half declared, half discovered at runtime."""

    connection: EigerConnection

    # Declared (checked): must exist, with this access mode and dtype, once
    # build() has turned what the device reports into attributes. ``state``
    # is discrete, and its enum class is built from the ``allowed_values`` the
    # device reports, so there is no author-time type to hint - only the access
    # mode can be pinned here.
    count_time: AttrRW[float]
    state: AttrR

    # Derived (soft): built on top of the discovered ``state`` param. Declaring
    # ``state`` as a checked attribute is what lets us reference it in code and
    # publish something computed from it - here, whether the detector is idle.
    idle: AttrR[bool]

    def __init__(self, connections: Connections) -> None:
        self.connection = connections.get("eiger", EigerConnection)
        super().__init__()

    def _getter(self, subsystem: Subsystem, param: str):
        async def get() -> Any:
            # No cast here - ``update`` validates against the datatype, which is the
            # one place a bad value from the device should be coerced or complained
            # about.
            return await self.connection.get_parameter(subsystem, param)

        return get

    def _setter(self, subsystem: Subsystem, param: str):
        async def put(value: Any) -> None:
            await self.connection.put_parameter(subsystem, param, value)

        return put

    async def _walk(self) -> list[ParameterInfo]:
        """Ask the detector what it has.

        The connection is open by the time ``build`` runs, so this is a plain
        sequence of reads - the shape of the tree is whatever the device answers
        with on this particular startup.
        """
        parameters: list[ParameterInfo] = []
        for subsystem in SUBSYSTEMS:
            for param in await self.connection.keys(subsystem):
                data = await self.connection.describe(subsystem, param)
                allowed_values = data.get("allowed_values")
                parameters.append(
                    ParameterInfo(
                        subsystem=subsystem,
                        name=param,
                        value_type=data["value_type"],
                        access_mode=data["access_mode"],
                        allowed_values=(
                            None if allowed_values is None else tuple(allowed_values)
                        ),
                    )
                )
        return parameters

    async def build(self) -> None:
        """Turn what the device reports into attributes."""
        for parameter in await self._walk():
            datatype = _datatype(parameter)
            getter = self._getter(parameter.subsystem, parameter.name)
            setter = None

            if parameter.access_mode == "rw":
                setter = self._setter(parameter.subsystem, parameter.name)
            else:
                # Read-only params are status values that change on the device,
                # so poll them periodically rather than reading once.
                getter = Polled(getter, period=UPDATE_PERIOD)

            declaration = self.filler.declarations.get(parameter.name)
            if declaration is not None and declaration.child is not None:
                # A parameter the class body declared already exists as an
                # unfilled attribute, so provision that one rather than adding a
                # second of the same name. The filler checks the access mode and
                # datatype the hint promised against what the device turned out
                # to report.
                self.filler.fill_attribute(
                    parameter.name, datatype=datatype, getter=getter, setter=setter
                )
            elif setter is None:
                self.add_attribute(parameter.name, AttrR(datatype, getter=getter))
            else:
                self.add_attribute(
                    parameter.name, AttrRW(datatype, getter=getter, setter=setter)
                )

        # Every hinted parameter should have turned up in the tree the device
        # reported.
        self.filler.check_filled()

        # Keep the derived ``idle`` flag in sync with the discovered ``state``.
        self.state.add_readback_callback(self._update_idle)

    async def _update_idle(self, state: enum.Enum) -> None:
        await self.idle.update(state.value == "idle")
