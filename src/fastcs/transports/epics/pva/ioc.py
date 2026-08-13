import asyncio

from p4p.server import Server, StaticProvider

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.transports.epics.util import pv_prefix_from_path
from fastcs.util import snake_to_pascal

from ._pv_handlers import make_command_pv, make_shared_read_pv, make_shared_write_pv
from .pvi import add_pvi_info


def parse_attributes(root_controller_api: ControllerAPI) -> StaticProvider:
    """Parses `Attribute` s into p4p signals in handlers."""
    provider = StaticProvider(pv_prefix_from_path(root_controller_api.path))

    for controller_api in root_controller_api.walk_api():
        pv_prefix = pv_prefix_from_path(controller_api.path)
        add_pvi_info(
            provider=provider,
            pv_prefix=pv_prefix,
            controller_api=controller_api,
            description=controller_api.description,
        )

        for attr_name, attribute in controller_api.attributes.items():
            full_pv_name = f"{pv_prefix}:{snake_to_pascal(attr_name)}"
            match attribute:
                case AttrRW():
                    attribute_pv = make_shared_write_pv(attribute)
                    attribute_pv_rbv = make_shared_read_pv(attribute)
                    provider.add(f"{full_pv_name}", attribute_pv)
                    provider.add(f"{full_pv_name}_RBV", attribute_pv_rbv)
                case AttrR():
                    attribute_pv = make_shared_read_pv(attribute)
                    provider.add(f"{full_pv_name}", attribute_pv)
                case AttrW():
                    attribute_pv = make_shared_write_pv(attribute)
                    provider.add(f"{full_pv_name}", attribute_pv)

        for attr_name, method in controller_api.command_methods.items():
            full_pv_name = f"{pv_prefix}:{snake_to_pascal(attr_name)}"
            command_pv = make_command_pv(method.fn)
            provider.add(f"{full_pv_name}", command_pv)

    return provider


class P4PIOC:
    """A P4P IOC which handles one or more controllers.

    Each controller gets its own ``StaticProvider`` so it exposes an independent
    ``:PVI`` root with no super-parent.
    """

    def __init__(self, controller_apis: list[ControllerAPI]):
        self._controller_apis = controller_apis
        # Build the PVs up front rather than in ``run``. Creating a PV is what
        # registers its readback/setpoint callbacks on the attribute, and ``run``
        # is awaited after the initial polls have already fired - so a PV built
        # there would miss the first readback, and the setpoint an ``AttrRW``
        # seeds from it (ADR 0020), leaving the served value at the datatype
        # default. ``EpicsCAIOC`` builds its records in ``__init__`` for the
        # same reason.
        self._providers = [parse_attributes(api) for api in self._controller_apis]

    async def run(self):
        endless_event = asyncio.Event()
        with Server(self._providers):
            await endless_event.wait()
