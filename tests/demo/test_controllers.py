from unittest.mock import AsyncMock

import numpy as np
import pytest

from fastcs.connections import IPConnectionSettings
from fastcs.controllers import ControllerVector
from fastcs.demo.controllers import (
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
    controller.connection.send_command = AsyncMock()  # type: ignore[method-assign]

    await controller.cancel_all()

    sent_commands = [
        call.args[0] for call in controller.connection.send_command.call_args_list
    ]
    for index in controller.ramps:
        assert f"N{index:02d}=0\r\n" in sent_commands


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
