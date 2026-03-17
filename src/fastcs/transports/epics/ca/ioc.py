import asyncio
from typing import Any, Literal

from softioc import builder, softioc
from softioc.asyncio_dispatcher import AsyncioDispatcher
from softioc.pythonSoftIoc import RecordWrapper

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import DType_T, Waveform
from fastcs.logging import logger
from fastcs.methods import Command
from fastcs.tracer import Tracer
from fastcs.transports.epics.ca.util import (
    _make_in_record,
    _make_out_record,
    cast_from_epics_type,
    cast_to_epics_type,
)
from fastcs.transports.epics.util import controller_pv_prefix
from fastcs.util import snake_to_pascal

EPICS_MAX_NAME_LENGTH = 60


tracer = Tracer()


class EpicsCAIOC:
    """A softioc which handles a controller"""

    def __init__(
        self,
        pv_prefix: str,
        controller_api: ControllerAPI,
    ):
        self._controller_api = controller_api
        _add_pvi_info(f"{pv_prefix}:PVI")
        _add_sub_controller_pvi_info(pv_prefix, controller_api)

        _create_and_link_attribute_pvs(pv_prefix, controller_api)
        _create_and_link_command_pvs(pv_prefix, controller_api)

    def run(self) -> None:
        dispatcher = AsyncioDispatcher(asyncio.get_running_loop())
        builder.LoadDatabase()
        softioc.iocInit(dispatcher)


def _add_pvi_info(
    pvi: str,
    parent_pvi: str = "",
    name: str = "",
):
    """Add PVI metadata for a controller.

    Args:
        pvi: PVI PV of controller
        parent_pvi: PVI PV of parent controller
        name: Name to register controller with parent as

    """
    # Create a record to attach the info tags to
    record = builder.longStringIn(
        f"{pvi}_PV",
        initial_value=pvi,
        DESC="The records in this controller",
    )

    # Create PVI PV in preparation for adding attribute info tags to it
    q_group = {
        pvi: {
            "+id": "epics:nt/NTPVI:1.0",
            "display.description": {"+type": "plain", "+channel": "DESC"},
            "": {"+type": "meta", "+channel": "VAL"},
        }
    }
    # If this controller has a parent, add a link in the parent to this controller
    if parent_pvi and name:
        q_group.update(
            {
                parent_pvi: {
                    f"value.{name}.d": {
                        "+channel": "VAL",
                        "+type": "plain",
                        "+trigger": f"value.{name}.d",
                    }
                }
            }
        )

    record.add_info("Q:group", q_group)


def _add_sub_controller_pvi_info(pv_prefix: str, parent: ControllerAPI):
    """Add PVI references from controller to its sub controllers, recursively.

    Args:
        pv_prefix: PV Prefix of IOC
        parent: Controller to add PVI refs for

    """
    parent_pvi = f"{controller_pv_prefix(pv_prefix, parent)}:PVI"

    for child in parent.sub_apis.values():
        child_pvi = f"{controller_pv_prefix(pv_prefix, child)}:PVI"
        child_name = (
            f"__{child.path[-1]}"  # Sub-Controller of ControllerVector
            if child.path[-1].isdigit()
            else child.path[-1]
        )

        _add_pvi_info(child_pvi, parent_pvi, child_name.lower())

        _add_sub_controller_pvi_info(pv_prefix, child)


def _create_and_link_attribute_pvs(
    root_pv_prefix: str, root_controller_api: ControllerAPI
) -> None:
    for controller_api in root_controller_api.walk_api():
        pv_prefix = controller_pv_prefix(root_pv_prefix, controller_api)

        for attr_name, attribute in controller_api.attributes.items():
            if (
                isinstance(attribute.datatype, Waveform)
                and len(attribute.datatype.shape) != 1
            ):
                logger.warning(
                    "Only 1D Waveform attributes are supported in EPICS CA transport",
                    attribute=attribute,
                )
                continue

            pv_name = snake_to_pascal(attr_name)
            full_pv_name_length = len(f"{pv_prefix}:{pv_name}")
            if full_pv_name_length > EPICS_MAX_NAME_LENGTH:
                attribute.enabled = False
                logger.warning(
                    f"Not creating PV for {attr_name} for controller"
                    f" {controller_api.path} as full name would exceed"
                    f" {EPICS_MAX_NAME_LENGTH} characters"
                )
                continue

            match attribute:
                case AttrRW():
                    if full_pv_name_length > (EPICS_MAX_NAME_LENGTH - 4):
                        logger.warning(
                            f"Not creating PVs for {attr_name} as _RBV PV"
                            f" name would exceed {EPICS_MAX_NAME_LENGTH}"
                            " characters"
                        )
                        attribute.enabled = False
                    else:
                        _create_and_link_read_pv(
                            pv_prefix, f"{pv_name}_RBV", attr_name, attribute
                        )
                        _create_and_link_write_pv(
                            pv_prefix, pv_name, attr_name, attribute
                        )
                case AttrR():
                    _create_and_link_read_pv(pv_prefix, pv_name, attr_name, attribute)
                case AttrW():
                    _create_and_link_write_pv(pv_prefix, pv_name, attr_name, attribute)


def _create_and_link_read_pv(
    pv_prefix: str, pv_name: str, attr_name: str, attribute: AttrR[DType_T]
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def async_record_set(value: DType_T):
        tracer.log_event(
            "PV set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value))

    record = _make_in_record(pv, attribute)
    _add_attr_pvi_info(record, pv_prefix, attr_name, "r")

    attribute.add_on_update_callback(async_record_set)


def _create_and_link_write_pv(
    pv_prefix: str, pv_name: str, attr_name: str, attribute: AttrW[DType_T]
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def on_update(value):
        logger.info("PV put: {pv} = {value}", pv=pv, value=repr(value))

        await attribute.put(cast_from_epics_type(attribute.datatype, value))

    async def set_setpoint_without_process(value: DType_T):
        tracer.log_event(
            "PV setpoint set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value), process=False)

    record = _make_out_record(pv, attribute, on_update=on_update)

    _add_attr_pvi_info(record, pv_prefix, attr_name, "w")

    attribute.add_sync_setpoint_callback(set_setpoint_without_process)


def _create_and_link_command_pvs(
    root_pv_prefix: str, root_controller_api: ControllerAPI
) -> None:
    for controller_api in root_controller_api.walk_api():
        pv_prefix = controller_pv_prefix(root_pv_prefix, controller_api)

        for attr_name, method in controller_api.command_methods.items():
            pv_name = snake_to_pascal(attr_name)

            if len(f"{pv_prefix}:{pv_name}") > EPICS_MAX_NAME_LENGTH:
                print(
                    f"Not creating PV for {attr_name} as full name would exceed"
                    f" {EPICS_MAX_NAME_LENGTH} characters"
                )
                method.enabled = False
            else:
                _create_and_link_command_pv(
                    pv_prefix,
                    pv_name,
                    attr_name,
                    method,
                )


def _create_and_link_command_pv(
    pv_prefix: str, pv_name: str, attr_name: str, method: Command
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def wrapped_method(_: Any):
        tracer.log_event("Command PV put", topic=method, pv=pv)

        await method.fn()

    record = builder.Action(
        f"{pv_prefix}:{pv_name}",
        on_update=wrapped_method,
        blocking=True,
        initial_value=0,
        ZNAM="Idle",
        ONAM="Active",
    )

    _add_attr_pvi_info(record, pv_prefix, attr_name, "x")


def _add_attr_pvi_info(
    record: RecordWrapper,
    prefix: str,
    name: str,
    access_mode: Literal["r", "w", "rw", "x"],
):
    """Add an info tag to a record to include it in the PVI for the controller.

    Args:
        record: Record to add info tag to
        prefix: PV prefix of controller
        name: Name of parameter to add to PVI
        access_mode: Access mode of parameter

    """
    record.add_info(
        "Q:group",
        {
            f"{prefix}:PVI": {
                f"value.{name}.{access_mode}": {
                    "+channel": "NAME",
                    "+type": "plain",
                    "+trigger": f"value.{name}.{access_mode}",
                }
            }
        },
    )
