import asyncio

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from fastcs.attributes import AttrR, AttrRW
from fastcs.demo.eiger import UPDATE_PERIOD, EigerDetector
from fastcs.demo.simulation.eiger import API_PREFIX, create_eiger_sim_app
from fastcs.util import ONCE


@pytest.fixture
def sim_client() -> TestClient:
    return TestClient(create_eiger_sim_app())


def test_sim_lists_keys(sim_client: TestClient):
    response = sim_client.get(f"{API_PREFIX}/config/keys")
    assert response.status_code == 200
    assert set(response.json()) >= {"count_time", "frame_time", "nimages"}


def test_sim_get_parameter(sim_client: TestClient):
    response = sim_client.get(f"{API_PREFIX}/config/count_time")
    assert response.status_code == 200
    body = response.json()
    assert body == {"value": 0.1, "value_type": "float", "access_mode": "rw"}


def test_sim_put_parameter(sim_client: TestClient):
    response = sim_client.put(f"{API_PREFIX}/config/count_time", json={"value": 0.5})
    assert response.status_code == 200
    assert response.json() == {"value": 0.5}

    response = sim_client.get(f"{API_PREFIX}/config/count_time")
    assert response.json()["value"] == 0.5


def test_sim_put_read_only_parameter_rejected(sim_client: TestClient):
    response = sim_client.put(f"{API_PREFIX}/status/state", json={"value": "busy"})
    assert response.status_code == 403


def test_sim_unknown_parameter_404(sim_client: TestClient):
    assert sim_client.get(f"{API_PREFIX}/config/nonexistent").status_code == 404
    assert sim_client.get(f"{API_PREFIX}/nonexistent/keys").status_code == 404


@pytest_asyncio.fixture
async def detector() -> EigerDetector:
    transport = httpx.ASGITransport(app=create_eiger_sim_app())
    controller = EigerDetector(transport=transport)
    await controller.connect()
    await controller.initialise()
    controller.post_initialise()
    return controller


@pytest.mark.asyncio
async def test_hinted_attributes_are_introspected(detector: EigerDetector):
    assert isinstance(detector.count_time, AttrRW)
    assert detector.count_time.datatype.dtype is float

    assert isinstance(detector.state, AttrR)
    assert detector.state.datatype.dtype is str


@pytest.mark.asyncio
async def test_unhinted_attributes_are_also_introspected(detector: EigerDetector):
    for name in ("frame_time", "nimages", "description", "temperature", "humidity"):
        assert name in detector.attributes


@pytest.mark.asyncio
async def test_read_attribute_from_device(detector: EigerDetector):
    await detector.count_time.bind_update_callback()()
    assert detector.count_time.get() == 0.1

    temperature = detector.attributes["temperature"]
    assert isinstance(temperature, AttrR)
    await temperature.bind_update_callback()()
    assert temperature.get() == 22.5


@pytest.mark.asyncio
async def test_write_attribute_to_device(detector: EigerDetector):
    await detector.count_time.put(0.5)

    response = await detector.connection.get("config", "count_time")
    assert response["value"] == 0.5


@pytest.mark.asyncio
async def test_idle_derived_from_state(detector: EigerDetector):
    # ``idle`` is soft and starts at its default, tracking ``state`` once polled.
    assert detector.idle.get() is False

    await detector.state.update("idle")
    assert detector.idle.get() is True

    await detector.state.update("acquire")
    assert detector.idle.get() is False


@pytest.mark.asyncio
async def test_read_only_params_poll_but_rw_read_once(detector: EigerDetector):
    for name in ("state", "temperature", "humidity", "description"):
        attr = detector.attributes[name]
        assert isinstance(attr, AttrR) and not isinstance(attr, AttrRW)
        assert attr.io_ref.update_period == UPDATE_PERIOD

    assert detector.count_time.io_ref.update_period is ONCE


@pytest.mark.asyncio
async def test_sim_temperature_oscillates():
    # The background task only runs under the app lifespan (a real server), not the
    # bare ASGI transport used elsewhere, so drive the lifespan explicitly here.
    app = create_eiger_sim_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(base_url="http://sim", transport=transport) as c:
            readings = []
            for _ in range(4):
                await asyncio.sleep(0.3)
                response = await c.get(f"{API_PREFIX}/status/temperature")
                readings.append(response.json()["value"])

    assert len(set(readings)) > 1, f"temperature did not change: {readings}"
