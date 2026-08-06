import numpy as np
from p4p import Value
from p4p.nt import NTEnum, NTNDArray, NTScalar, NTTable
from p4p.nt.enum import ntenum
from p4p.nt.ndarray import ntndarray
from p4p.server import ServerOperation
from p4p.server.asyncio import SharedPV

from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.datatypes import Enum, Table
from fastcs.methods import CommandCallback
from fastcs.tracer import Tracer

from .types import (
    MAJOR_ALARM_SEVERITY,
    RECORD_ALARM_STATUS,
    cast_from_p4p_value,
    cast_to_p4p_value,
    make_p4p_type,
    p4p_alarm_states,
    p4p_timestamp_now,
)

tracer = Tracer()


class WritePvHandler:
    def __init__(self, attr_w: AttrW | AttrRW):
        self._attr_w = attr_w

    async def put(self, pv: SharedPV, op: ServerOperation):
        value = op.value()
        if isinstance(self._attr_w.datatype, Table):
            assert isinstance(value, list)
            raw_value = np.array(
                [tuple(labelled_row.values()) for labelled_row in value],
                dtype=self._attr_w.datatype.structured_dtype,
            )
        elif isinstance(value, Value):
            raw_value = value.todict()["value"]
        else:
            # Unfortunately these types don't have a `todict`,
            # while our `buildType` fields don't have a `.raw`.
            assert isinstance(value, ntenum | ntndarray)
            raw_value = value.raw.value  # type:ignore

        cast_value = cast_from_p4p_value(self._attr_w, raw_value)

        tracer.log_event("PV put", topic=self._attr_w, pv=pv, value=cast_value)

        if isinstance(self._attr_w.datatype, Enum):
            pv.post(cast_to_p4p_value(self._attr_w, cast_value))
        else:
            pv.post(value)

        await self._attr_w.put(cast_value)
        op.done()


class CommandPvHandler:
    def __init__(self, command: CommandCallback):
        self._command = command
        self._task_in_progress = False

    async def _run_command(self) -> dict:
        self._task_in_progress = True

        try:
            await self._command()
        except Exception as e:
            alarm_states = p4p_alarm_states(
                MAJOR_ALARM_SEVERITY, RECORD_ALARM_STATUS, str(e)
            )
        else:
            alarm_states = p4p_alarm_states()

        self._task_in_progress = False
        return alarm_states

    async def put(self, pv: SharedPV, op: ServerOperation):
        value = op.value()
        raw_value = value["value"]

        if raw_value is True:
            if self._task_in_progress:
                raise RuntimeError(
                    "Received request to run command but it is already in progress. "
                    "Maybe the command should spawn an asyncio task?"
                )

            # Check if record block request recieved
            match op.pvRequest().todict():
                case {"record": {"_options": {"block": "true"}}}:
                    blocking = True
                case _:
                    blocking = False

            # Flip to true once command task starts
            pv.post({"value": True, **p4p_timestamp_now(), **p4p_alarm_states()})
            if not blocking:
                op.done()
            alarm_states = await self._run_command()
            pv.post({"value": False, **p4p_timestamp_now(), **alarm_states})
            if blocking:
                op.done()
        else:
            raise RuntimeError("Commands should only take the value `True`.")


def _make_shared_pv_arguments(attribute: Attribute) -> dict[str, object]:
    type_ = make_p4p_type(attribute)
    if isinstance(type_, (NTEnum | NTNDArray | NTTable)):
        return {"nt": type_}
    else:

        def _wrap(value: dict):
            return Value(type_, value)

        return {"wrap": _wrap}


def make_shared_read_pv(attribute: AttrR) -> SharedPV:
    shared_pv = SharedPV(
        initial=cast_to_p4p_value(attribute, attribute.get()),
        **_make_shared_pv_arguments(attribute),
    )

    async def set_readback(value):
        tracer.log_event("PV set readback", topic=attribute, value=value)
        shared_pv.post(cast_to_p4p_value(attribute, value))

    attribute.add_on_update_callback(set_readback)

    return shared_pv


def make_shared_write_pv(attribute: AttrW) -> SharedPV:
    shared_pv = SharedPV(
        handler=WritePvHandler(attribute),
        initial=cast_to_p4p_value(attribute, attribute.datatype.initial_value),
        **_make_shared_pv_arguments(attribute),
    )

    async def set_setpoint(value):
        tracer.log_event("PV set setpoint", topic=attribute, value=value)
        shared_pv.post(cast_to_p4p_value(attribute, value))

    attribute.add_sync_setpoint_callback(set_setpoint)

    return shared_pv


def make_command_pv(command: CommandCallback) -> SharedPV:
    type_ = NTScalar.buildType("?", display=True, control=True)

    initial = Value(type_, {"value": False, **p4p_alarm_states()})

    def _wrap(value: dict):
        return Value(type_, value)

    shared_pv = SharedPV(
        initial=initial,
        handler=CommandPvHandler(command),
        wrap=_wrap,
    )

    return shared_pv
