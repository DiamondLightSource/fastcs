from unittest.mock import AsyncMock

import numpy as np
import pytest

from fastcs.attributes import AttrW
from fastcs.connections import IPConnectionSettings
from fastcs.controllers import ControllerVector
from fastcs.demo.temperature_attr import (
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

    await controller.ramp_rate.poll()

    controller.connection.send_query.assert_awaited_once_with("R?\r\n")
    assert controller.ramp_rate.readback == 1.5


@pytest.mark.asyncio
async def test_ramp_rate_written_to_device(controller: TemperatureController):
    controller.connection.send_command = AsyncMock()

    await controller.ramp_rate.set(2.5)

    controller.connection.send_command.assert_awaited_once_with("R=2.5\r\n")


@pytest.mark.asyncio
async def test_power_read_from_device(controller: TemperatureController):
    controller.connection.send_query = AsyncMock(return_value="10.25\r\n")

    await controller.power.poll()

    controller.connection.send_query.assert_awaited_once_with("P?\r\n")
    assert controller.power.readback == 10.25


@pytest.mark.asyncio
async def test_ramp_start_read_from_device(ramp_controller: TemperatureRampController):
    ramp_controller.connection.send_query = AsyncMock(return_value="7\r\n")

    await ramp_controller.start.poll()

    ramp_controller.connection.send_query.assert_awaited_once_with("S01?\r\n")
    assert ramp_controller.start.readback == 7


@pytest.mark.asyncio
async def test_ramp_end_written_to_device(ramp_controller: TemperatureRampController):
    ramp_controller.connection.send_command = AsyncMock()

    await ramp_controller.end.set(42)

    ramp_controller.connection.send_command.assert_awaited_once_with("E01=42\r\n")


@pytest.mark.asyncio
async def test_ramp_enabled_written_to_device(
    ramp_controller: TemperatureRampController,
):
    ramp_controller.connection.send_command = AsyncMock()

    await ramp_controller.enabled.set(OnOffEnum.On)

    ramp_controller.connection.send_command.assert_awaited_once_with("N01=1\r\n")


@pytest.mark.asyncio
async def test_each_ramp_addresses_its_own_index(controller: TemperatureController):
    controller.connection.send_command = AsyncMock()

    for index, ramp in controller.ramps.items():
        await ramp.start.set(index)

    assert [
        call.args[0] for call in controller.connection.send_command.await_args_list
    ] == ["S01=1\r\n", "S02=2\r\n", "S03=3\r\n", "S04=4\r\n"]


@pytest.mark.asyncio
async def test_read_only_attribute_has_no_setter(
    ramp_controller: TemperatureRampController,
):
    # Access mode is structural now: no setter means it is not an AttrW at all.
    assert not isinstance(ramp_controller.target, AttrW)
    assert ramp_controller.start.has_setter()


@pytest.mark.asyncio
async def test_cancel_all_disables_every_ramp(controller: TemperatureController):
    sets = {}
    for index, ramp in controller.ramps.items():
        sets[index] = AsyncMock()
        ramp.enabled.set = sets[index]  # type: ignore[method-assign]

    await controller.cancel_all()

    for set_ in sets.values():
        set_.assert_awaited_once_with(OnOffEnum.Off)


@pytest.mark.asyncio
async def test_update_voltages_updates_waveform_and_each_ramp(
    controller: TemperatureController,
):
    controller.connection.send_query = AsyncMock(return_value="[1, 2, 3, 4]\r\n")

    await controller.update_voltages()

    controller.connection.send_query.assert_awaited_once_with("V?\r\n")
    np.testing.assert_array_equal(
        controller.voltages.readback, np.array([1, 2, 3, 4], dtype=np.int32)
    )
    for index, ramp in controller.ramps.items():
        assert ramp.voltage.readback == pytest.approx(float(index))
