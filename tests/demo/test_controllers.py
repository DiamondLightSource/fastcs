from unittest.mock import AsyncMock

import numpy as np
import pytest

from fastcs.connections import IPConnectionSettings
from fastcs.controllers import ControllerVector
from fastcs.demo.controllers import (
    OnOffEnum,
    TemperatureController,
    TemperatureControllerSettings,
    TemperatureRampController,
)


@pytest.fixture
def controller() -> TemperatureController:
    settings = TemperatureControllerSettings(
        num_ramp_controllers=4,
        ip_settings=IPConnectionSettings(ip="localhost", port=25565),
    )
    controller = TemperatureController(settings)
    controller.post_initialise()
    return controller


@pytest.fixture
def ramp_controller(controller: TemperatureController) -> TemperatureRampController:
    return controller.ramps[1]


def test_ramps_is_controller_vector(controller: TemperatureController):
    assert isinstance(controller.ramps, ControllerVector)
    assert list(controller.ramps) == [1, 2, 3, 4]
    for index, ramp in controller.ramps.items():
        assert isinstance(ramp, TemperatureRampController)
        assert controller.ramps[index] is ramp


@pytest.mark.asyncio
async def test_ramp_rate_read_from_device(controller: TemperatureController):
    controller.connection.send_query = AsyncMock(return_value="1.5\r\n")

    await controller.ramp_rate.bind_update_callback()()

    controller.connection.send_query.assert_awaited_once_with("R?\r\n")
    assert controller.ramp_rate.get() == 1.5


@pytest.mark.asyncio
async def test_ramp_rate_written_to_device(controller: TemperatureController):
    controller.connection.send_command = AsyncMock()

    await controller.ramp_rate.put(2.5)

    controller.connection.send_command.assert_awaited_once_with("R=2.5\r\n")


@pytest.mark.asyncio
async def test_power_read_from_device(controller: TemperatureController):
    controller.connection.send_query = AsyncMock(return_value="10.25\r\n")

    await controller.power.bind_update_callback()()

    controller.connection.send_query.assert_awaited_once_with("P?\r\n")
    assert controller.power.get() == 10.25


@pytest.mark.asyncio
async def test_ramp_start_read_from_device(ramp_controller: TemperatureRampController):
    ramp_controller.connection.send_query = AsyncMock(return_value="7\r\n")

    await ramp_controller.start.bind_update_callback()()

    ramp_controller.connection.send_query.assert_awaited_once_with("S01?\r\n")
    assert ramp_controller.start.get() == 7


@pytest.mark.asyncio
async def test_ramp_end_written_to_device(ramp_controller: TemperatureRampController):
    ramp_controller.connection.send_command = AsyncMock()

    await ramp_controller.end.put(42)

    ramp_controller.connection.send_command.assert_awaited_once_with("E01=42\r\n")


@pytest.mark.asyncio
async def test_ramp_enabled_written_to_device(
    ramp_controller: TemperatureRampController,
):
    ramp_controller.connection.send_command = AsyncMock()

    await ramp_controller.enabled.put(OnOffEnum.On)

    ramp_controller.connection.send_command.assert_awaited_once_with("N01=1\r\n")


@pytest.mark.asyncio
async def test_each_ramp_addresses_its_own_index(controller: TemperatureController):
    controller.connection.send_command = AsyncMock()

    for index, ramp in controller.ramps.items():
        await ramp.start.put(index)

    assert [
        call.args[0] for call in controller.connection.send_command.await_args_list
    ] == ["S01=1\r\n", "S02=2\r\n", "S03=3\r\n", "S04=4\r\n"]


@pytest.mark.asyncio
async def test_read_only_attribute_has_no_write_command(
    ramp_controller: TemperatureRampController,
):
    assert ramp_controller.target.io_ref.write_cmd is None


@pytest.mark.asyncio
async def test_cancel_all_disables_every_ramp(controller: TemperatureController):
    puts = {}
    for index, ramp in controller.ramps.items():
        puts[index] = AsyncMock()
        ramp.enabled.put = puts[index]  # type: ignore[method-assign]

    await controller.cancel_all()

    for put in puts.values():
        put.assert_awaited_once_with(OnOffEnum.Off, sync_setpoint=True)


@pytest.mark.asyncio
async def test_update_voltages_updates_waveform_and_each_ramp(
    controller: TemperatureController,
):
    controller.connection.send_query = AsyncMock(return_value="[1, 2, 3, 4]\r\n")

    await controller.update_voltages()

    controller.connection.send_query.assert_awaited_once_with("V?\r\n")
    np.testing.assert_array_equal(
        controller.voltages.get(), np.array([1, 2, 3, 4], dtype=np.int32)
    )
    for index, ramp in controller.ramps.items():
        assert ramp.voltage.get() == pytest.approx(float(index))
