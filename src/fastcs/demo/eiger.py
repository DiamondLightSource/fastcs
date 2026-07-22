"""Example 5 - introspectable controller: a cut-down Eiger over the fake REST sim.

Half the attributes (``count_time``, ``state``) are declared as type hints and
checked by the current ``HintedAttribute`` introspection-validation mechanism; the
rest of the parameter tree is discovered at ``initialise()`` time by walking the
sim's ``keys`` endpoints and is added dynamically, with no static check. A device
that describes itself over the wire is exactly the case where introspection earns
its complexity - contrast with the (deliberately non-introspectable) SCPI/temperature
examples.
"""

from dataclasses import KW_ONLY, dataclass
from typing import Any

import httpx

from fastcs.attributes import AnyAttributeIO, AttributeIO, AttributeIORef, AttrR, AttrRW
from fastcs.controllers import Controller
from fastcs.datatypes import Bool, DataType, Float, Int, String
from fastcs.demo.simulation.eiger import API_PREFIX, Subsystem, ValueType
from fastcs.util import ONCE

_DATATYPES: dict[ValueType, type[DataType]] = {
    "float": Float,
    "int": Int,
    "string": String,
    "bool": Bool,
}


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


@dataclass
class EigerAttributeIORef(AttributeIORef):
    subsystem: Subsystem
    param: str
    _: KW_ONLY
    update_period: float | None = ONCE


class EigerAttributeIO(AttributeIO[Any, EigerAttributeIORef]):
    def __init__(self, connection: EigerConnection):
        super().__init__()
        self._connection = connection

    async def update(self, attr: AttrR[Any, EigerAttributeIORef]) -> None:
        data = await self._connection.get(attr.io_ref.subsystem, attr.io_ref.param)
        await attr.update(attr.dtype(data["value"]))

    async def send(self, attr, value) -> None:
        await self._connection.put(attr.io_ref.subsystem, attr.io_ref.param, value)


class EigerDetector(Controller):
    """Cut-down Eiger controller: half declared, half introspected."""

    # Declared (checked): must exist, with this access mode and dtype, after
    # initialise() introspects the parameter tree.
    count_time: AttrRW[float]
    state: AttrR[str]

    def __init__(
        self,
        settings: EigerConnectionSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.connection = EigerConnection(transport=transport)
        ios: list[AnyAttributeIO] = [EigerAttributeIO(self.connection)]
        super().__init__(ios=ios)

        self._settings = settings or EigerConnectionSettings()

    async def connect(self) -> None:
        await self.connection.connect(self._settings)
        self._connected = True

    async def disconnect(self) -> None:
        await self.connection.close()

    async def initialise(self) -> None:
        for subsystem in ("config", "status"):
            for param in await self.connection.keys(subsystem):
                data = await self.connection.get(subsystem, param)
                datatype_cls = _DATATYPES[data["value_type"]]
                io_ref = EigerAttributeIORef(subsystem=subsystem, param=param)

                if data["access_mode"] == "rw":
                    attr = AttrRW(datatype_cls(), io_ref=io_ref)
                else:
                    attr = AttrR(datatype_cls(), io_ref=io_ref)

                self.add_attribute(param, attr)
