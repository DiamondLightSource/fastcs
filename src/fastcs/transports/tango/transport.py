import asyncio
from dataclasses import dataclass, field

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport

from .dsr import TangoDSR, TangoDSROptions
from .util import tango_dev_class_name, validate_tango_id


@dataclass
class TangoTransport(Transport):
    """Tango transport."""

    tango: TangoDSROptions = field(default_factory=TangoDSROptions)

    def connect(
        self,
        controller_apis: list[ControllerAPI],
        loop: asyncio.AbstractEventLoop,
    ):
        seen: dict[str, str] = {}
        for api in controller_apis:
            id = api.path[0]
            validate_tango_id(id)
            cls_name = tango_dev_class_name(id)
            if cls_name in seen:
                raise ValueError(
                    f"Controller ids {seen[cls_name]!r} and {id!r} both sanitise "
                    f"to Tango device-class name {cls_name!r}; pick one variant "
                    "(hyphens and underscores are not distinguishable in Tango "
                    "class names)"
                )
            seen[cls_name] = id
        self._dsr = TangoDSR(controller_apis, loop)

    async def serve(self) -> None:
        coro = asyncio.to_thread(self._dsr.run, self.tango)
        await coro
