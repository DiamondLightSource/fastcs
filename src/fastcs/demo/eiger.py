"""Example 5 - introspectable controller: a cut-down Eiger over the fake REST sim.

Half the attributes (``count_time``, ``state``) are declared as type hints and
checked by the current ``HintedAttribute`` introspection-validation mechanism; the
rest of the parameter tree is discovered by walking the sim's ``keys`` endpoints and
is added dynamically, with no static check. A device that describes itself over the
wire is exactly the case where introspection earns its complexity - contrast with the
(deliberately non-introspectable) SCPI/temperature examples.

The introspection happens in `EigerConnection.connect`, not in the controller, which
is what earns the reconnect check: the connection returns a `DetectorInfo` that the
framework keeps and compares on every reconnect, so a detector that comes back
describing itself differently is caught rather than served stale.
"""

import enum
from dataclasses import dataclass
from typing import Any, cast

import httpx

from fastcs.attributes import AttrR, AttrRW, Polled
from fastcs.connections import Connection
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


@dataclass(frozen=True)
class ParameterInfo:
    """What the device says about one of its parameters.

    Deliberately the *shape* of the parameter and not its value: the value changes
    every time it is read, and this is compared against the startup value on every
    reconnect.
    """

    subsystem: Subsystem
    name: str
    value_type: ValueType
    access_mode: str
    allowed_values: tuple[str, ...] | None


@dataclass(frozen=True)
class DetectorInfo:
    """Returned by `EigerConnection.connect`.

    Compared against the startup value on every reconnect, so it must compare by
    value - hence a frozen dataclass of plain fields rather than the raw JSON.
    """

    parameters: tuple[ParameterInfo, ...]


def _datatype(info: ParameterInfo) -> type[DType]:
    """Build a datatype for a parameter from the metadata the device reports.

    A parameter that reports ``allowed_values`` is discrete, so it becomes an enum
    class built from those values. The members are only knowable over the wire,
    which is exactly the case introspection exists for.
    """
    if info.allowed_values is None:
        return _DATATYPES[info.value_type]

    name = "".join(part.title() for part in info.name.split("_"))
    # The functional API builds a class; type checkers only see the instance signature.
    return cast(
        type[enum.Enum],
        enum.Enum(name, {value: value for value in info.allowed_values}),
    )


@dataclass
class EigerConnectionSettings:
    base_url: str = "http://localhost:8000"


class EigerConnection(Connection[DetectorInfo]):
    """HTTP to the Eiger REST sim, and the one thing that knows when it is down.

    A ``transport`` can be supplied to point directly at an in-process ASGI app
    (e.g. in tests), bypassing the network entirely.

    Args:
        settings: Where the detector's REST API lives
        transport: Optional httpx transport, for talking to an in-process app
        kwargs: Passed to `Connection`

    """

    def __init__(
        self,
        settings: EigerConnectionSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or EigerConnectionSettings()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> DetectorInfo:
        """Open the client and ask the detector what it has.

        Introspecting here rather than in a controller's ``build`` is what lets the
        framework compare the answer on every reconnect.
        """
        self._client = httpx.AsyncClient(
            base_url=self._settings.base_url, transport=self._transport
        )

        parameters: list[ParameterInfo] = []
        for subsystem in SUBSYSTEMS:
            for param in await self.keys(subsystem):
                data = await self.get(subsystem, param)
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

        return DetectorInfo(parameters=tuple(parameters))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("EigerConnection is not connected")
        return self._client

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Every request goes through here, because this is where health is decided.

        A transport error means the link is gone and everything holding this
        connection is now down. A 4xx from the detector is a device complaint about
        one parameter, and propagates to the caller without touching connection
        state.
        """
        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.TransportError:
            self.set_disconnected()
            raise

        response.raise_for_status()
        return response

    async def keys(self, subsystem: Subsystem) -> list[str]:
        response = await self._request("GET", f"{API_PREFIX}/{subsystem}/keys")
        return response.json()

    async def get(self, subsystem: Subsystem, param: str) -> dict:
        response = await self._request("GET", f"{API_PREFIX}/{subsystem}/{param}")
        return response.json()

    async def put(self, subsystem: Subsystem, param: str, value) -> None:
        await self._request(
            "PUT", f"{API_PREFIX}/{subsystem}/{param}", json={"value": value}
        )


class EigerDetector(Controller):
    """Cut-down Eiger controller: half declared, half introspected."""

    connection: EigerConnection

    # Declared (checked): must exist, with this access mode and dtype, after
    # build() has turned the connection's introspection into attributes. ``state``
    # is discrete, and its enum class is built from the ``allowed_values`` the
    # device reports, so there is no author-time type to hint - only the access
    # mode can be pinned here.
    count_time: AttrRW[float]
    state: AttrR

    # Derived (soft): built on top of the introspected ``state`` param. Declaring
    # ``state`` as a checked attribute is what lets us reference it in code and
    # publish something computed from it - here, whether the detector is idle.
    idle = AttrR(bool)

    def __init__(
        self,
        settings: EigerConnectionSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.connection = EigerConnection(settings, transport)
        super().__init__()

    def _getter(self, subsystem: Subsystem, param: str):
        async def get() -> Any:
            data = await self.connection.get(subsystem, param)
            # No cast here - ``update`` validates against the datatype, which is the
            # one place a bad value from the device should be coerced or complained
            # about.
            return data["value"]

        return get

    def _setter(self, subsystem: Subsystem, param: str):
        async def put(value: Any) -> None:
            await self.connection.put(subsystem, param, value)

        return put

    async def build(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, info: DetectorInfo
    ) -> None:
        """Turn what the connection found into attributes.

        The argument is whatever ``EigerConnection.connect`` returned - a controller
        that does not introspect writes ``build(self)`` instead.
        """
        for parameter in info.parameters:
            datatype = _datatype(parameter)
            getter = self._getter(parameter.subsystem, parameter.name)

            if parameter.access_mode == "rw":
                attr: AttrR = AttrRW(
                    datatype,
                    getter=getter,
                    setter=self._setter(parameter.subsystem, parameter.name),
                )
            else:
                # Read-only params are status values that change on the device,
                # so poll them periodically rather than reading once.
                attr = AttrR(datatype, getter=Polled(getter, period=UPDATE_PERIOD))

            self.add_attribute(parameter.name, attr)

        # Keep the derived ``idle`` flag in sync with the introspected ``state``.
        self.state.add_readback_callback(self._update_idle)

    async def _update_idle(self, state: enum.Enum) -> None:
        await self.idle.update(state.value == "idle")
