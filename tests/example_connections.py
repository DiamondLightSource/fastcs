"""Example connections, and controllers holding them, for the runner tests"""

from dataclasses import dataclass

from fastcs.connections import Connection, ConnectionSpec, Introspection
from fastcs.controllers import Controller
from fastcs.methods import scan
from fastcs.util import ONCE


class RecordingConnection(Connection):
    reconnect_attempts = 10
    reconnect_period = 0

    def __init__(self, events: list[str], name: str = "") -> None:
        self.events = events
        self.suffix = f" {name}" if name else ""

    async def connect(self) -> None:
        self.events.append(f"connect{self.suffix}")

    async def close(self) -> None:
        self.events.append(f"close{self.suffix}")


class HoldingController(Controller):
    def __init__(self, connection: Connection) -> None:
        super().__init__()
        self.connection = connection
        self.reads = 0

    @scan(ONCE)
    async def read_once(self) -> None:
        self.reads += 1


class EigerConnection(RecordingConnection):
    pass


class OdinConnection(RecordingConnection):
    depends_on = EigerConnection


class TwoConnectionController(Controller):
    def __init__(self, odin: OdinConnection, eiger: EigerConnection) -> None:
        super().__init__()
        self.ODIN = HoldingController(odin)
        self.EIGER = HoldingController(eiger)


@dataclass
class DeviceIntrospection:
    parameters: dict[str, int]


class IntrospectingConnection(Connection[DeviceIntrospection]):
    reconnect_attempts = 10
    reconnect_period = 0

    async def connect(self) -> None:
        pass

    async def introspect(self) -> Introspection[DeviceIntrospection]:
        return Introspection(DeviceIntrospection({"gain": 2}))

    async def close(self) -> None:
        pass


class IntrospectedController(Controller):
    def __init__(self, connection: IntrospectingConnection) -> None:
        super().__init__()
        self.connection = connection
        self.parameters = connection.introspection.parameters


class MetaConnection(Connection):
    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass


class DetectorConnection(MetaConnection):
    async def introspect(self) -> Introspection:
        subconnections = [
            ConnectionSpec("meta", MetaConnection),
            ConnectionSpec("unused", MetaConnection),
        ]
        return Introspection(None, subconnections=subconnections)


class DetectorController(Controller):
    def __init__(
        self, detector: DetectorConnection, detector_meta: MetaConnection | None = None
    ) -> None:
        super().__init__()
        self.detector = detector
        self.meta = HoldingController(detector_meta) if detector_meta else None
