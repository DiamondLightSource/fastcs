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
