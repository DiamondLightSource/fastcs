import asyncio
import enum

import httpx
import pytest
import pytest_asyncio

from fastcs.attributes import AttrR, AttrRW
from fastcs.demo.eiger import UPDATE_PERIOD, EigerDetector
from fastcs.demo.simulation.eiger import EigerParameter, create_eiger_sim_app
from fastcs.util import ONCE

# Backdoor to the sim's parameter tree, keyed by subsystem then param name.
SimState = dict[str, dict[str, EigerParameter]]


@pytest_asyncio.fixture
async def _eiger():
    app = create_eiger_sim_app()
    controller = EigerDetector(transport=httpx.ASGITransport(app=app))
    await controller.connect()
    await controller.initialise()
    controller.post_initialise()
    yield controller, app.state.sim
    await controller.disconnect()


@pytest_asyncio.fixture
async def detector(_eiger) -> EigerDetector:
    return _eiger[0]


@pytest_asyncio.fixture
async def sim(_eiger) -> SimState:
    return _eiger[1]


@pytest.mark.asyncio
async def test_hinted_attributes_are_introspected(detector: EigerDetector):
    assert isinstance(detector.count_time, AttrRW)
    assert detector.count_time.dtype is float

    assert isinstance(detector.state, AttrR)
    # ``state`` reports ``allowed_values``, so it is introspected as an enum whose
    # members come from the device rather than as a bare string.
    assert issubclass(detector.state.dtype, enum.Enum)
    assert [member.name for member in detector.state.dtype] == [
        "idle",
        "ready",
        "acquire",
    ]


@pytest.mark.asyncio
async def test_enum_attribute_reads_as_member(detector: EigerDetector, sim: SimState):
    sim["status"]["state"].value = "acquire"
    await detector.state.poll()

    state = detector.state.readback
    assert isinstance(state, enum.Enum)
    assert state.value == "acquire"


@pytest.mark.asyncio
async def test_unhinted_attributes_are_also_introspected(detector: EigerDetector):
    for name in ("frame_time", "nimages", "description", "temperature", "humidity"):
        assert name in detector.attributes


@pytest.mark.asyncio
async def test_read_attribute_from_device(detector: EigerDetector):
    await detector.count_time.poll()
    assert detector.count_time.readback == 0.1

    humidity = detector.attributes["humidity"]
    assert isinstance(humidity, AttrR)
    await humidity.poll()
    assert humidity.readback == 32.1


@pytest.mark.asyncio
async def test_write_attribute_to_device(detector: EigerDetector):
    await detector.count_time.set(0.5)

    # Read it back through the attribute to confirm the round-trip to the device.
    await detector.count_time.poll()
    assert detector.count_time.readback == 0.5


@pytest.mark.asyncio
async def test_idle_derived_from_state(detector: EigerDetector, sim: SimState):
    # ``idle`` is soft and starts at its default, tracking ``state`` once polled.
    assert detector.idle.readback is False

    # Poke the read-only ``state`` via the sim backdoor, then poll the attribute.
    sim["status"]["state"].value = "acquire"
    await detector.state.poll()
    assert detector.idle.readback is False

    sim["status"]["state"].value = "idle"
    await detector.state.poll()
    assert detector.idle.readback is True


@pytest.mark.asyncio
async def test_read_only_params_poll_but_rw_read_once(detector: EigerDetector):
    for name in ("state", "temperature", "humidity", "description"):
        attr = detector.attributes[name]
        assert isinstance(attr, AttrR) and not isinstance(attr, AttrRW)
        assert attr.poll_period == UPDATE_PERIOD

    assert detector.count_time.poll_period is ONCE


@pytest.mark.asyncio
async def test_temperature_oscillation_seen_via_subscribe():
    # The oscillation task runs under the app lifespan, so drive the lifespan here
    # (the bare ASGI transport used elsewhere does not start it). Observe it through
    # the controller's temperature attribute, subscribing for updates.
    app = create_eiger_sim_app()
    async with app.router.lifespan_context(app):
        controller = EigerDetector(transport=httpx.ASGITransport(app=app))
        await controller.connect()
        await controller.initialise()
        controller.post_initialise()

        temperature = controller.attributes["temperature"]
        assert isinstance(temperature, AttrR)

        seen: list[float] = []

        async def record(value: float) -> None:
            seen.append(value)

        temperature.add_readback_callback(record)

        # Poll across several sim flips (every 0.5s) so the value changes under us.
        for _ in range(8):
            await temperature.poll()
            await asyncio.sleep(0.2)

        await controller.disconnect()

    assert len(set(seen)) > 1, f"temperature did not change: {seen}"
