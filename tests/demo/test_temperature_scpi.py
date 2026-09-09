from typing import Annotated
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controllers import ControllerVector
from fastcs.demo.scpi import SCPIController, SCPIParam
from fastcs.demo.temperature_scpi import (
    OnOffEnum,
    TemperatureController,
    TemperatureControllerSettings,
    TemperatureRampController,
)


@pytest_asyncio.fixture
async def controller() -> TemperatureController:
    settings = TemperatureControllerSettings(
        num_ramp_controllers=4,
        ip_settings=IPConnectionSettings(ip="localhost", port=25565),
    )
    controller = TemperatureController(settings)

    # What `ControllerRunner` does before anything is served: the declarations
    # are provisioned by `initialise`, not by construction.
    await controller.initialise()
    for ramp in controller.ramps.values():
        await ramp.initialise()

    return controller


@pytest_asyncio.fixture
async def ramp_controller(
    controller: TemperatureController,
) -> TemperatureRampController:
    return controller.ramps[1]


@pytest.mark.asyncio
async def test_ramps_is_controller_vector(controller: TemperatureController):
    assert isinstance(controller.ramps, ControllerVector)
    assert list(controller.ramps) == [1, 2, 3, 4]
    for index, ramp in controller.ramps.items():
        assert isinstance(ramp, TemperatureRampController)
        assert controller.ramps[index] is ramp


@pytest.mark.asyncio
async def test_every_declaration_was_provisioned(controller: TemperatureController):
    controller.check_filled()

    assert set(controller.attributes) == {"ramp_rate", "power", "voltages"}
    assert set(controller.ramps[1].attributes) == {
        "start",
        "end",
        "enabled",
        "target",
        "actual",
        "voltage",
    }


@pytest.mark.asyncio
async def test_the_hint_decides_the_access_mode(controller: TemperatureController):
    assert isinstance(controller.ramp_rate, AttrW)
    # `power: AttrR[float]` has no setter half for the filler to provision
    assert not isinstance(controller.power, AttrW)


@pytest.mark.asyncio
async def test_metadata_comes_from_the_param(controller: TemperatureController):
    """The command token and the metadata are written in one place, and both end
    up on the attribute."""
    assert controller.ramp_rate.meta == {
        "precision": 3,
        "units": "K/s",
        "description": "Rate of change",
    }


@pytest.mark.asyncio
async def test_an_attribute_without_a_param_is_left_to_the_driver(
    controller: TemperatureController,
):
    """`voltages` is updated by the scan, so it has no command token - the
    controller fills it itself with the shape only it knows."""
    assert controller.voltages.meta.get("shape") == (4,)
    assert not controller.voltages.has_getter()


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
async def test_a_ramp_suffixes_every_command(
    ramp_controller: TemperatureRampController,
):
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
async def test_an_enum_datatype_comes_from_the_hint(
    ramp_controller: TemperatureRampController,
):
    """`AttrRW[OnOffEnum]` is what parses the device's `0`/`1` into a member."""
    ramp_controller.connection.send_query = AsyncMock(return_value="1\r\n")

    await ramp_controller.enabled.poll()

    assert ramp_controller.enabled.readback is OnOffEnum.On


@pytest.mark.asyncio
async def test_each_ramp_addresses_its_own_index(controller: TemperatureController):
    controller.connection.send_command = AsyncMock()

    for index, ramp in controller.ramps.items():
        await ramp.start.set(index)

    assert [
        call.args[0] for call in controller.connection.send_command.await_args_list
    ] == ["S01=1\r\n", "S02=2\r\n", "S03=3\r\n", "S04=4\r\n"]


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


@pytest.mark.asyncio
async def test_metadata_is_validated_against_the_declared_datatype():
    """The runtime counterpart to the static `Unpack[FloatMeta]` check: a spec
    object collects whatever it was told, so what it holds is only checkable when
    it meets the datatype the hint declared."""

    class Mislabelled(SCPIController):
        serial: Annotated[AttrR[str], SCPIParam("SN", precision=3)]

    controller = Mislabelled(IPConnection())

    with pytest.raises(TypeError) as error:
        await controller.initialise()

    assert "'precision' is not valid metadata for str" in str(error.value)
    assert "serial" in str(error.value)


@pytest.mark.asyncio
async def test_a_param_on_a_hint_that_names_no_datatype():
    """A SCPI device cannot be asked what it holds, so the hint has to say."""

    class Underspecified(SCPIController):
        state: Annotated[AttrR, SCPIParam("ST")]

    controller = Underspecified(IPConnection())

    with pytest.raises(TypeError, match="does not say what it holds"):
        await controller.initialise()


@pytest.mark.asyncio
async def test_a_hint_with_no_param_is_reported_as_unfilled():
    """`SCPIController` fills what carries a token; `check_filled` still holds
    the driver to everything else its class body promised."""

    class Forgetful(SCPIController):
        discovered: AttrR

    controller = Forgetful(IPConnection())

    with pytest.raises(RuntimeError, match="discovered"):
        await controller.initialise()


@pytest.mark.asyncio
async def test_the_poll_period_is_a_class_attribute():
    class Slow(SCPIController):
        poll_period = 5.0

        reading: Annotated[AttrRW[float], SCPIParam("X")]

    controller = Slow(IPConnection())
    await controller.initialise()

    assert controller.reading.poll_period == 5.0
