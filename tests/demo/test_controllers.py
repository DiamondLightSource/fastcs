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


def test_ramps_is_controller_vector(controller: TemperatureController):
    assert isinstance(controller.ramps, ControllerVector)
    assert list(controller.ramps) == [1, 2, 3, 4]
    for index, ramp in controller.ramps.items():
        assert isinstance(ramp, TemperatureRampController)
        assert controller.ramps[index] is ramp


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

    np.testing.assert_array_equal(
        controller.voltages.get(), np.array([1, 2, 3, 4], dtype=np.int32)
    )
    for index, ramp in controller.ramps.items():
        assert ramp.voltage.get() == pytest.approx(float(index))
