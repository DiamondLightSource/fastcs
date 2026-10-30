import asyncio
from multiprocessing import Queue

import pytest
from aioca import FORMAT_TIME, caget, camonitor, caput
from softioc import alarm


@pytest.mark.asyncio
async def test_ioc(softioc_subprocess: tuple[str, Queue]):
    pv_prefix, _ = softioc_subprocess

    # Assert alias
    assert await caget(f"{pv_prefix}:B") == await caget(f"{pv_prefix}:AliasB") == 0
    await caput(f"{pv_prefix}:B", 10, wait=True)
    assert await caget(f"{pv_prefix}:AliasB") == 10
    await caput(f"{pv_prefix}:AliasB", 20, wait=True)
    assert await caget(f"{pv_prefix}:B") == 20
    b_rbv = await caget(f"{pv_prefix}:B_RBV")
    alias_b_rbv = await caget(f"{pv_prefix}:AliasB_RBV")
    assert b_rbv == alias_b_rbv == 20

    # Assert command exceptions set record alarm states. The command record
    # reverts back to False once the (failing) command completes.
    d_values: asyncio.Queue = asyncio.Queue()
    subscription = camonitor(
        f"{pv_prefix}:ChildVector:0:D", d_values.put_nowait, format=FORMAT_TIME
    )
    try:
        assert await d_values.get() == 0  # First monitor value
        await caput(f"{pv_prefix}:ChildVector:0:D", True)
        d_value = await d_values.get()
        assert d_value.severity == alarm.MAJOR_ALARM  # First real call fails
        await caput(f"{pv_prefix}:ChildVector:0:D", True)
        d_value = await d_values.get()
        assert d_value.severity == alarm.NO_ALARM  # Second real call succeeds
    finally:
        subscription.close()


@pytest.mark.asyncio
async def test_enum_mapping_alias(softioc_subprocess: tuple[str, Queue]):
    pv_prefix, _ = softioc_subprocess
    e = f"{pv_prefix}:ChildVector:0:E"
    alias_e = f"{pv_prefix}:EnumAliasE"

    # Initial value of 'Active' (2) is mapped to 'On'
    assert await caget(f"{e}_RBV", datatype=str) == "Active"
    assert await caget(alias_e, datatype=str) == "On"
    assert await caget(f"{alias_e}_RBV", datatype=str) == "On"

    # Put to alias puts the mapped value to the attribute
    await caput(alias_e, "Off", wait=True)
    assert await caget(e, datatype=str) == "Idle"
    assert await caget(f"{e}_RBV", datatype=str) == "Idle"
    assert await caget(f"{alias_e}_RBV", datatype=str) == "Off"

    # Put to attribute syncs the alias setpoint and readback
    await caput(e, "Active", wait=True)
    assert await caget(alias_e, datatype=str) == "On"
    assert await caget(f"{alias_e}_RBV", datatype=str) == "On"
    assert await caget(f"{e}_RBV", datatype=str) == "Active"


async def _get_count(pv: str) -> int:
    value = await caget(pv)
    assert isinstance(value, int)
    return value


@pytest.mark.asyncio
async def test_command_alias(softioc_subprocess: tuple[str, Queue]):
    pv_prefix, _ = softioc_subprocess
    alias_d = f"{pv_prefix}:EnumAliasD"
    d_calls = [f"{pv_prefix}:ChildVector:{i}:DCalls" for i in range(2)]
    initial = [await _get_count(pv) for pv in d_calls]

    # 'Active' triggers only ChildVector:0:D
    await caput(alias_d, "Active", wait=True)
    assert [await _get_count(pv) for pv in d_calls] == [initial[0] + 1, initial[1]]

    # 'Idle' triggers only ChildVector:1:D, which shares the alias PV
    await caput(alias_d, "Idle", wait=True)
    assert [await _get_count(pv) for pv in d_calls] == [initial[0] + 1, initial[1] + 1]
