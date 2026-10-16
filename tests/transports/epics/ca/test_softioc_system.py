import asyncio
from multiprocessing import Queue

import pytest
from aioca import DBR_CHAR_STR, FORMAT_CTRL, FORMAT_TIME, caget, camonitor, caput
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
async def test_hinted_enums(softioc_subprocess: tuple[str, Queue]):
    pv_prefix, _ = softioc_subprocess

    async def choices(pv: str) -> list[str]:
        return list((await caget(f"{pv_prefix}:{pv}", format=FORMAT_CTRL)).enums)

    # Served choices are the device's: Superset drops AUTO, Subset adds SOFTWARE
    assert await choices("Mode_RBV") == ["OFF", "ON"]
    assert await choices("Gain_RBV") == ["LOW", "HIGH"]
    assert await choices("Trigger_RBV") == ["INTERNAL", "EXTERNAL", "SOFTWARE"]

    # A choice only the device has can be set and read back
    await caput(f"{pv_prefix}:Trigger", "SOFTWARE", wait=True)
    assert await caget(f"{pv_prefix}:Trigger_RBV", datatype=str) == "SOFTWARE"

    # >16 choices are served as a long string record of names
    long_rbv = f"{pv_prefix}:LongTrigger_RBV"
    assert await caget(long_rbv, datatype=DBR_CHAR_STR) == "INTERNAL"
    await caput(f"{pv_prefix}:LongTrigger", "LINE3", datatype=DBR_CHAR_STR, wait=True)
    assert await caget(long_rbv, datatype=DBR_CHAR_STR) == "LINE3"
