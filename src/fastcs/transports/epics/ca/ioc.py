import asyncio
from collections import Counter
from collections.abc import Awaitable
from typing import Any

from softioc import alarm, builder, softioc
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
from fastcs.transports.epics.util import EPICS_MAX_NAME_LENGTH, pv_prefix_from_path
from fastcs.util import snake_to_pascal

tracer = Tracer()

RBV_SUFFIX = "_RBV"


class EpicsCAIOC:
    """A softioc which handles one or more controllers."""

    def __init__(self, controller_apis: list[ControllerAPI], aliases: dict[str, str]):
        if duplicate_aliases := [
            alias for alias, count in Counter(aliases.values()).items() if count > 1
        ]:
            raise RuntimeError(
                "Failed to create EPICS CA IOC, as duplicate aliases were provided:"
                f" {duplicate_aliases}"
            )

        self._controller_apis = controller_apis
        for controller_api in controller_apis:
            _create_and_link_attribute_pvs(controller_api, aliases)
            _create_and_link_command_pvs(controller_api, aliases)

    def run(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        dispatcher = AsyncioDispatcher(loop)  # Needs running loop
        builder.LoadDatabase()
        softioc.iocInit(dispatcher, enable_pva=False)


def _create_and_link_attribute_pvs(
    root_controller_api: ControllerAPI, aliases: dict[str, str]
) -> None:
    for controller_api in root_controller_api.walk_api():
        pv_prefix = pv_prefix_from_path(controller_api.path)

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

            alias = aliases.get(f"{pv_prefix}:{pv_name}", None)
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
                        alias_rbv = aliases.get(
                            f"{pv_prefix}:{pv_name}{RBV_SUFFIX}", None
                        )
                        _create_and_link_read_pv(
                            pv_prefix,
                            f"{pv_name}{RBV_SUFFIX}",
                            alias_rbv,
                            attribute,
                        )
                        _create_and_link_write_pv(
                            pv_prefix,
                            pv_name,
                            alias,
                            attribute,
                        )
                case AttrR():
                    _create_and_link_read_pv(
                        pv_prefix,
                        pv_name,
                        alias,
                        attribute,
                    )
                case AttrW():
                    _create_and_link_write_pv(
                        pv_prefix,
                        pv_name,
                        alias,
                        attribute,
                    )


def _create_and_link_read_pv(
    pv_prefix: str,
    pv_name: str,
    alias: str | None,
    attribute: AttrR[DType_T],
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def async_record_set(value: DType_T):
        tracer.log_event(
            "PV set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value))

    record = _make_in_record(pv, attribute)

    _add_alias(record, alias)

    attribute.add_on_update_callback(async_record_set)


def _create_and_link_write_pv(
    pv_prefix: str,
    pv_name: str,
    alias: str | None,
    attribute: AttrW[DType_T],
):
    pv = f"{pv_prefix}:{pv_name}"

    async def on_update(value):
        logger.info("PV put: {pv} = {value}", pv=pv, value=repr(value))
        await _run_and_set_alarm(
            record, attribute.put(cast_from_epics_type(attribute.datatype, value))
        )

    async def set_setpoint_without_process(value: DType_T):
        tracer.log_event(
            "PV setpoint set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value), process=False)

    record = _make_out_record(pv, attribute, on_update=on_update)

    _add_alias(record, alias)

    attribute.add_sync_setpoint_callback(set_setpoint_without_process)


def _create_and_link_command_pvs(
    root_controller_api: ControllerAPI, aliases: dict[str, str]
) -> None:
    for controller_api in root_controller_api.walk_api():
        pv_prefix = pv_prefix_from_path(controller_api.path)

        for attr_name, method in controller_api.command_methods.items():
            pv_name = snake_to_pascal(attr_name)
            alias = aliases.get(f"{pv_prefix}:{pv_name}", None)

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
                    alias,
                    method,
                )


def _create_and_link_command_pv(
    pv_prefix: str, pv_name: str, alias: str | None, method: Command
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def wrapped_method(_: Any):
        tracer.log_event("Command PV put", topic=method, pv=pv)
        await _run_and_set_alarm(record, method.fn())

    record = builder.Action(
        f"{pv_prefix}:{pv_name}",
        on_update=wrapped_method,
        blocking=True,
        initial_value=0,
        ZNAM="Idle",
        ONAM="Active",
    )

    _add_alias(record, alias)


def _add_alias(record: RecordWrapper, alias: str | None):
    if alias is not None:
        if len(alias) > EPICS_MAX_NAME_LENGTH:
            logger.warning(
                f"Not creating alias {alias}, as full name would exceed"
                f" {EPICS_MAX_NAME_LENGTH} characters"
            )
        else:
            record.add_alias(alias)


def _set_alarm(record: RecordWrapper, alarm_state: int):
    record.set(
        record.get(),
        process=False,
        severity=alarm_state,
        alarm=alarm_state,
    )


async def _run_and_set_alarm(record, coro: Awaitable):
    """Await `coro` and update `record`'s alarm state based on the outcome.

    On success, clears the alarm (NO_ALARM). On any exception, raises the
    record into MAJOR_ALARM. The exception itself is not re-raised or
    logged here, since `AttrW.put` already logs it; this function's only
    job is to reflect the outcome in the record's alarm status.
    """
    try:
        await coro
        _set_alarm(record, alarm.NO_ALARM)
    except Exception:
        _set_alarm(record, alarm.MAJOR_ALARM)
