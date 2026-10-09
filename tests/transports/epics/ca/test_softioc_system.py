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

    # Assert enum alias
    e_pv = f"{pv_prefix}:ChildVector:0:E"
    assert await caget(e_pv) == 0
    assert await caget(e_pv, datatype=str) == "Invalid"  # Default for underlying enum
    assert await caget(f"{pv_prefix}:EnumAliasE") == 0
    assert await caget(f"{pv_prefix}:EnumAliasE", datatype=str) == "Off"

    await caput(f"{pv_prefix}:EnumAliasE", 1, wait=True)
    # 'On' is index 1, but maps to value '2'
    assert await caget(f"{pv_prefix}:EnumAliasE", datatype=str) == "On"
    assert await caget(e_pv) == 2  # Underlying enum attr gets put with 2
    assert await caget(e_pv, datatype=str) == "Active"
    assert await caget(f"{e_pv}_RBV", datatype=str) == "Active"

    await caput(e_pv, 1, wait=True)
    assert await caget(f"{e_pv}_RBV", datatype=str) == "Idle"
    # Aliased enum gets converted update
    assert await caget(f"{pv_prefix}:EnumAliasE_RBV", datatype=str) == "Off"
    assert await caget(f"{pv_prefix}:EnumAliasE", datatype=str) == "Off"

    # Assert command aliased to enum. 'Active' aliases to True on command 'D', which
    # fails on every other call
    enum_alias_d = f"{pv_prefix}:EnumAliasD"
    await caput(enum_alias_d, "Active", wait=True)
    assert (await caget(enum_alias_d, format=FORMAT_TIME)).severity == alarm.MAJOR_ALARM
    await caput(enum_alias_d, "Active", wait=True)
    assert (await caget(enum_alias_d, format=FORMAT_TIME)).severity == alarm.NO_ALARM
