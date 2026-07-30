from unittest.mock import AsyncMock

import pytest

from fastcs.connections import IPConnectionSettings
from fastcs.demo.temperature_attr import (
    TemperatureAttrController,
    TemperatureAttrSettings,
)


@pytest.fixture
def controller() -> TemperatureAttrController:
    settings = TemperatureAttrSettings(
        ip_settings=IPConnectionSettings(ip="localhost", port=25565)
    )
    controller = TemperatureAttrController(settings)
    controller.post_initialise()
    return controller


@pytest.mark.asyncio
async def test_ramp_rate_read_from_device(controller: TemperatureAttrController):
    controller.connection.send_query = AsyncMock(return_value="1.5\r\n")

    await controller.ramp_rate.bind_update_callback()()

    controller.connection.send_query.assert_awaited_once_with("R?\r\n")
    assert controller.ramp_rate.get() == 1.5


@pytest.mark.asyncio
async def test_ramp_rate_written_to_device(controller: TemperatureAttrController):
    controller.connection.send_command = AsyncMock()

    await controller.ramp_rate.put(2.5)

    controller.connection.send_command.assert_awaited_once_with("R=2.5\r\n")


@pytest.mark.asyncio
async def test_power_read_from_device(controller: TemperatureAttrController):
    controller.connection.send_query = AsyncMock(return_value="10.25\r\n")

    await controller.power.bind_update_callback()()

    controller.connection.send_query.assert_awaited_once_with("P?\r\n")
    assert controller.power.get() == 10.25
