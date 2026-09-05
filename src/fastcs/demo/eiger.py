"""Example 5 - introspectable controller: a cut-down Eiger over the fake REST sim.

Half the attributes (``count_time``, ``state``) are declared as type hints and
checked by the current ``HintedAttribute`` introspection-validation mechanism; the
rest of the parameter tree is discovered at ``initialise()`` time by walking the
sim's ``keys`` endpoints and is added dynamically, with no static check. A device
that describes itself over the wire is exactly the case where introspection earns
its complexity - contrast with the (deliberately non-introspectable) SCPI/temperature
examples.
"""

import enum
from dataclasses import dataclass
from typing import Any, cast

import httpx

from fastcs.attributes import AttrR, AttrRW, Polled
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


def _datatype(param: str, data: dict[str, Any]) -> type[DType]:
    """Build a datatype for a parameter from the metadata the device reports.

    A parameter that reports ``allowed_values`` is discrete, so it becomes an enum
    class built from those values. The members are only knowable over the wire,
    which is exactly the case introspection exists for.
    """
    allowed_values = data.get("allowed_values")
    if allowed_values is None:
        return _DATATYPES[data["value_type"]]

    name = "".join(part.title() for part in param.split("_"))
    # The functional API builds a class; type checkers only see the instance signature.
    return cast(
        type[enum.Enum], enum.Enum(name, {value: value for value in allowed_values})
    )


@dataclass
class EigerConnectionSettings:
    base_url: str = "http://localhost:8000"


class EigerConnection:
    """Thin async HTTP client wrapper for the Eiger REST sim.

    A ``transport`` can be supplied to point directly at an in-process ASGI app
    (e.g. in tests), bypassing the network entirely.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def connect(self, settings: EigerConnectionSettings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.base_url, transport=self._transport
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("EigerConnection is not connected")
        return self._client

    async def keys(self, subsystem: Subsystem) -> list[str]:
        response = await self.client.get(f"{API_PREFIX}/{subsystem}/keys")
        response.raise_for_status()
        return response.json()

    async def get(self, subsystem: Subsystem, param: str) -> dict:
        response = await self.client.get(f"{API_PREFIX}/{subsystem}/{param}")
        response.raise_for_status()
        return response.json()

    async def put(self, subsystem: Subsystem, param: str, value) -> None:
        response = await self.client.put(
            f"{API_PREFIX}/{subsystem}/{param}", json={"value": value}
        )
        response.raise_for_status()


class EigerDetector(Controller):
    """Cut-down Eiger controller: half declared, half introspected."""

    # Declared (checked): must exist, with this access mode and dtype, after
    # initialise() introspects the parameter tree. ``state`` is discrete, and its
    # enum class is built from the ``allowed_values`` the device reports, so there
    # is no author-time type to hint - only the access mode can be pinned here.
    count_time: AttrRW[float]
    state: AttrR

    # Derived (soft): built on top of the introspected ``state`` param. Declaring
    # ``state`` as a checked attribute is what lets us reference it in code and
    # publish something computed from it - here, whether the detector is idle.
    idle: AttrR[bool]

    def __init__(
        self,
        settings: EigerConnectionSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.connection = EigerConnection(transport=transport)
        super().__init__()

        self._settings = settings or EigerConnectionSettings()

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

    async def connect(self) -> None:
        await self.connection.connect(self._settings)
        self._connected = True

    async def disconnect(self) -> None:
        await self.connection.close()

    async def initialise(self) -> None:
        for subsystem in ("config", "status"):
            for param in await self.connection.keys(subsystem):
                data = await self.connection.get(subsystem, param)
                datatype = _datatype(param, data)

                if data["access_mode"] == "rw":
                    getter = self._getter(subsystem, param)
                    setter = self._setter(subsystem, param)
                else:
                    # Read-only params are status values that change on the device,
                    # so poll them periodically rather than reading once.
                    getter = Polled(
                        self._getter(subsystem, param), period=UPDATE_PERIOD
                    )
                    setter = None

                declaration = self.filler.declarations.get(param)
                if declaration is not None and declaration.child is not None:
                    # A parameter the class body declared already exists as an
                    # unfilled attribute, so provision that one rather than
                    # adding a second of the same name. The filler checks the
                    # access mode and datatype the hint promised against what
                    # the device turned out to report.
                    self.filler.fill_attribute(
                        param, datatype=datatype, getter=getter, setter=setter
                    )
                elif setter is None:
                    self.add_attribute(param, AttrR(datatype, getter=getter))
                else:
                    self.add_attribute(
                        param, AttrRW(datatype, getter=getter, setter=setter)
                    )

        # Every hinted parameter should have turned up in the tree the device
        # reported; say so with the source named if one did not.
        self.filler.check_filled("the Eiger REST parameter tree")

        # Keep the derived ``idle`` flag in sync with the introspected ``state``.
        self.state.add_readback_callback(self._update_idle)

    async def _update_idle(self, state: enum.Enum) -> None:
        await self.idle.update(state.value == "idle")
