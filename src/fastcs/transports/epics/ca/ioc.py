import asyncio
from collections import Counter
from collections.abc import Awaitable, Mapping
from enum import IntEnum
from typing import Any, Literal, TypeVar

from softioc import alarm, builder, softioc
from softioc.asyncio_dispatcher import AsyncioDispatcher
from softioc.pythonSoftIoc import RecordWrapper

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Bool, DataType, DType_T, Enum, Waveform
from fastcs.logging import logger
from fastcs.methods import Command
from fastcs.tracer import Tracer
from fastcs.transports.epics.ca.util import (
    _make_in_record,
    _make_out_record,
    cast_from_epics_type,
    cast_to_epics_type,
)
from fastcs.transports.epics.options import EnumMapping
from fastcs.transports.epics.util import EPICS_MAX_NAME_LENGTH, pv_prefix_from_path
from fastcs.util import snake_to_pascal

tracer = Tracer()
EnumT = TypeVar("EnumT", bound=IntEnum)
RBV_SUFFIX = "_RBV"


class EpicsCAIOC:
    """A softioc which handles one or more controllers."""

    def __init__(
        self,
        controller_apis: list[ControllerAPI],
        aliases: Mapping[str, str | EnumMapping],
    ):
        alias_pvs = [
            value if isinstance(value, str) else value.pv for value in aliases.values()
        ]

        if duplicate_aliases := [
            alias for alias, count in Counter(alias_pvs).items() if count > 1
        ]:
            raise RuntimeError(
                "Failed to create EPICS CA IOC, as duplicate aliases were provided:"
                f" {duplicate_aliases}"
            )

        self._controller_apis = controller_apis
        for controller_api in controller_apis:
            root_pv_prefix = pv_prefix_from_path(controller_api.path)
            _add_pvi_info(f"{root_pv_prefix}:PVI")
            _add_sub_controller_pvi_info(controller_api)

            _create_and_link_attribute_pvs(controller_api, aliases)
            _create_and_link_command_pvs(controller_api, aliases)

    def run(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        dispatcher = AsyncioDispatcher(loop)  # Needs running loop
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


def _add_sub_controller_pvi_info(parent: ControllerAPI):
    """Add PVI references from controller to its sub controllers, recursively."""
    parent_pvi = f"{pv_prefix_from_path(parent.path)}:PVI"

    for child in parent.sub_apis.values():
        child_pvi = f"{pv_prefix_from_path(child.path)}:PVI"
        child_name = (
            f"__{child.path[-1]}"  # Sub-Controller of ControllerVector
            if child.path[-1].isdigit()
            else child.path[-1]
        )

        _add_pvi_info(child_pvi, parent_pvi, child_name.lower())

        _add_sub_controller_pvi_info(child)


def _create_and_link_attribute_pvs(
    root_controller_api: ControllerAPI, aliases: Mapping[str, str | EnumMapping]
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
            if not _validate_pv_length(attr_name, f"{pv_prefix}:{pv_name}"):
                attribute.enabled = False
                continue

            alias = aliases.get(f"{pv_prefix}:{pv_name}", None)
            match attribute:
                case AttrRW():
                    rbv_pv = f"{pv_prefix}:{pv_name}{RBV_SUFFIX}"
                    if not _validate_pv_length(attr_name, rbv_pv):
                        attribute.enabled = False
                    else:
                        alias_rbv = aliases.get(rbv_pv, None)
                        _create_and_link_read_pv(
                            pv_prefix,
                            f"{pv_name}{RBV_SUFFIX}",
                            attr_name,
                            alias_rbv,
                            attribute,
                        )
                        _create_and_link_write_pv(
                            pv_prefix,
                            pv_name,
                            attr_name,
                            alias,
                            attribute,
                        )
                case AttrR():
                    _create_and_link_read_pv(
                        pv_prefix,
                        pv_name,
                        attr_name,
                        alias,
                        attribute,
                    )
                case AttrW():
                    _create_and_link_write_pv(
                        pv_prefix,
                        pv_name,
                        attr_name,
                        alias,
                        attribute,
                    )


def _create_and_link_read_pv(
    pv_prefix: str,
    pv_name: str,
    attr_name: str,
    alias: str | EnumMapping | None,
    attribute: AttrR[DType_T],
) -> None:
    pv = f"{pv_prefix}:{pv_name}"

    async def async_record_set(value: DType_T):
        tracer.log_event(
            "PV set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value))

    record = _make_in_record(pv, attribute)

    if isinstance(alias, str):
        _add_alias(record, alias, attr_name)
    elif isinstance(alias, EnumMapping):
        enum_attr = _get_read_enum_attr_from_type(alias)
        _add_read_enum_alias(alias, attribute, enum_attr)

    _add_attr_pvi_info(record, pv_prefix, attr_name, "r")
    attribute.add_on_update_callback(async_record_set)


def _sync_setpoint(pv: str, attribute: AttrW[DType_T], record: RecordWrapper) -> None:
    async def set_setpoint_without_process(value: DType_T):
        tracer.log_event(
            "PV setpoint set from attribute", topic=attribute, pv=pv, value=repr(value)
        )

        record.set(cast_to_epics_type(attribute.datatype, value), process=False)

    attribute.add_sync_setpoint_callback(set_setpoint_without_process)


def _create_and_link_write_pv(
    pv_prefix: str,
    pv_name: str,
    attr_name: str,
    alias: str | EnumMapping | None,
    attribute: AttrW[DType_T],
):
    pv = f"{pv_prefix}:{pv_name}"

    async def on_update(value):
        logger.info("PV put: {pv} = {value}", pv=pv, value=repr(value))
        cast_value = _cast_put(record, attribute.datatype, pv, value)
        if cast_value is None:
            return
        await _run_and_set_alarm(record, attribute.put(cast_value))

    record = _make_out_record(pv, attribute, on_update=on_update)

    if isinstance(alias, str):
        _add_alias(record, alias, attr_name)
    elif isinstance(alias, EnumMapping):
        enum_attr = _get_write_enum_attr_from_type(alias)
        _add_write_enum_alias(alias, attribute, enum_attr)

    _add_attr_pvi_info(record, pv_prefix, attr_name, "w")
    _sync_setpoint(pv, attribute, record)


def _create_and_link_command_pvs(
    root_controller_api: ControllerAPI, aliases: Mapping[str, str | EnumMapping]
) -> None:
    for controller_api in root_controller_api.walk_api():
        pv_prefix = pv_prefix_from_path(controller_api.path)

        for attr_name, method in controller_api.command_methods.items():
            pv_name = snake_to_pascal(attr_name)
            alias = aliases.get(f"{pv_prefix}:{pv_name}", None)

            if not _validate_pv_length(attr_name, f"{pv_prefix}:{pv_name}"):
                method.enabled = False
            else:
                _create_and_link_command_pv(
                    pv_prefix,
                    pv_name,
                    attr_name,
                    alias,
                    method,
                )


def _create_and_link_command_pv(
    pv_prefix: str,
    pv_name: str,
    attr_name: str,
    alias: str | EnumMapping | None,
    method: Command,
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

    if isinstance(alias, str):
        _add_alias(record, alias, attr_name)
    elif isinstance(alias, EnumMapping):
        enum_attr = _get_write_enum_attr_from_type(alias)
        _add_command_enum_alias(alias, method, enum_attr)

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


def _validate_pv_length(attribute_name: str, pv: str):
    if len(pv) > EPICS_MAX_NAME_LENGTH:
        logger.warning(
            f"Not creating PV '{pv}' for {attribute_name}, as full name would exceed"
            f" {EPICS_MAX_NAME_LENGTH} characters"
        )
        return False
    return True


def _add_alias(record: RecordWrapper, alias: str, attr_name: str):
    if alias is not None:
        if _validate_pv_length(attr_name, alias):
            record.add_alias(alias)


def _cast_put(
    record: RecordWrapper, datatype: DataType[DType_T], pv: str, value
) -> DType_T | None:
    """Cast an EPICS put value, or log and raise a MAJOR_ALARM if it is invalid."""
    try:
        return cast_from_epics_type(datatype, value)
    except (IndexError, ValueError) as e:
        logger.warning(
            "Ignoring put {value} to {pv} as it is invalid for {datatype}: {error}",
            value=value,
            pv=pv,
            datatype=datatype,
            error=e,
        )
        _set_alarm(record, alarm.MAJOR_ALARM)
        return None


def _set_alarm(record: RecordWrapper, alarm_state: int):
    record.set(
        record.get(),
        process=False,
        severity=alarm_state,
        alarm=alarm_state,
    )


async def _run_and_set_alarm(record: RecordWrapper, coro: Awaitable):
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


def _get_enum_from_alias(
    alias: EnumMapping,
):
    members = {name: i for i, name in enumerate(alias.mapping)}
    return IntEnum("enum", members)


def _get_read_enum_attr_from_type(alias: EnumMapping):
    enum = _get_enum_from_alias(alias)
    return AttrR(datatype=Enum(enum))


def _get_write_enum_attr_from_type(alias: EnumMapping):
    enum = _get_enum_from_alias(alias)
    return AttrW(datatype=Enum(enum))


def _resolve_mapping(
    alias: EnumMapping, datatype: DataType[DType_T]
) -> dict[str, DType_T] | None:
    """Convert the mapping values into values of the attribute's datatype.

    Logs a warning and returns None if the mapping is invalid for the datatype.
    """
    # Resolved once at creation, so datatype updates on aliased attributes are
    # not supported
    try:
        # validate casts loosely (e.g. bool("false") is True), this is accepted
        resolved = {
            name: datatype.validate(value) for name, value in alias.mapping.items()
        }
        names = _reverse_mapping(resolved)
    except (ValueError, TypeError) as e:
        logger.warning(
            "Not creating enum alias PV {pv}, as mapping is invalid for {datatype}: "
            "{error}",
            pv=alias.pv,
            datatype=datatype,
            error=e,
        )
        return None
    if len(names) != len(resolved):
        logger.warning(
            "Not creating enum alias PV {pv}, as mapping maps multiple names to the "
            "same value",
            pv=alias.pv,
            mapping=alias.mapping,
        )
        return None
    return resolved


def _reverse_mapping(resolved: dict[str, DType_T]) -> dict[DType_T, str]:
    return {value: name for name, value in resolved.items()}


def _add_command_enum_alias(
    alias: EnumMapping,
    method: Command,
    enum_attr: AttrW[EnumT],
):
    if not _validate_pv_length(str(method), alias.pv):
        return

    # validate casts loosely (e.g. bool("false") is True), this is accepted
    resolved = {name: Bool().validate(value) for name, value in alias.mapping.items()}

    async def trigger_command(value) -> None:
        logger.info("PV put: {pv} = {value}", pv=alias.pv, value=repr(value))
        cast_value = _cast_put(record, enum_attr.datatype, alias.pv, value)
        if cast_value is None:
            return
        await enum_attr.put(cast_value)

        if resolved[cast_value.name]:
            logger.info("Calling aliased command")
            await _run_and_set_alarm(record, method.fn())

    record = _make_out_record(alias.pv, enum_attr, on_update=trigger_command)
    _sync_setpoint(alias.pv, enum_attr, record)


def _add_read_enum_alias(
    alias: EnumMapping, attribute: AttrR[DType_T], enum_attr: AttrR[EnumT]
):
    if not _validate_pv_length(attribute.name, alias.pv):
        return

    enum = enum_attr.datatype.dtype
    resolved = _resolve_mapping(alias, attribute.datatype)
    if resolved is None:
        return
    names = _reverse_mapping(resolved)

    async def convert_from_value(value) -> None:
        converted_value = names.get(value)

        if converted_value is not None:
            validated_value = enum[converted_value]
            tracer.log_event(
                "PV set from attribute", topic=attribute, pv=alias.pv, value=repr(value)
            )
            record.set(cast_to_epics_type(enum_attr.datatype, validated_value))
            logger.info(
                "Converting to enum value {enum_value} from fastcs value {value}",
                enum_value=validated_value,
                value=value,
            )
            await enum_attr.update(validated_value)
        else:
            logger.warning(
                "Ignoring enum update as fastcs value {value} has no "
                "corresponding value in enum mapping",
                value=value,
                mapping=alias.mapping,
            )

    record = _make_in_record(alias.pv, enum_attr)
    initial_name = names.get(attribute.get())
    if initial_name is not None:
        record.set(cast_to_epics_type(enum_attr.datatype, enum[initial_name]))
    attribute.add_on_update_callback(convert_from_value)


def _add_write_enum_alias(
    alias: EnumMapping,
    attribute: AttrW[DType_T],
    enum_attr: AttrW[EnumT],
):
    if not _validate_pv_length(attribute.name, alias.pv):
        return

    resolved = _resolve_mapping(alias, attribute.datatype)
    if resolved is None:
        return
    names = _reverse_mapping(resolved)

    async def convert_to_value(value) -> None:
        logger.info("PV put: {pv} = {value}", pv=alias.pv, value=repr(value))
        cast_value = _cast_put(record, enum_attr.datatype, alias.pv, value)
        if cast_value is None:
            return
        await enum_attr.put(cast_value)
        converted_value = resolved[cast_value.name]
        logger.info(
            "Converting enum value {enum_value} to fastcs value {converted_value}",
            enum_value=value,
            converted_value=converted_value,
        )
        await _run_and_set_alarm(record, attribute.put(converted_value))

    record = _make_out_record(alias.pv, enum_attr, on_update=convert_to_value)
    _sync_setpoint(alias.pv, enum_attr, record)

    async def update_enum_alias(value: DType_T) -> None:
        converted_value = names.get(value)

        if converted_value is not None:
            enum_value = enum_attr.datatype.dtype[converted_value]
            record.set(
                cast_to_epics_type(enum_attr.datatype, enum_value),
                process=False,
            )
        else:
            logger.warning(
                "Ignoring enum setpoint sync as fastcs value {value} has no "
                "corresponding value in enum mapping",
                value=value,
                mapping=alias.mapping,
            )

    if isinstance(attribute, AttrR):
        attribute.add_on_update_callback(update_enum_alias)
