"""Tests for the multi-controller foundation slice (#353).

These tests exercise the public Controller.id lifecycle and
multi-controller REST routing through RestTransport, up to the
end-to-end FastCS.serve() lifecycle with two controllers.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from fastcs.attributes import AttrR
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.datatypes import Int
from fastcs.transports.rest.transport import RestTransport


class _IdController(Controller):
    pass


class _OneAttrController(Controller):
    foo = AttrR(Int())


class _OtherAttrController(Controller):
    bar = AttrR(Int())


def test_id_raises_before_set():
    controller = _IdController()
    with pytest.raises(RuntimeError, match="id"):
        _ = controller.id


def test_id_returns_value_after_set():
    controller = _IdController()
    controller.set_id("foo")
    assert controller.id == "foo"


def test_set_id_twice_raises():
    controller = _IdController()
    controller.set_id("foo")
    with pytest.raises(RuntimeError, match="already"):
        controller.set_id("bar")


def test_repr_includes_id_when_set():
    controller = _IdController()
    assert "id=" not in repr(controller)
    controller.set_id("foo")
    assert "id='foo'" in repr(controller)


def test_controller_api_path_uses_id():
    controller = _IdController()
    sub = _IdController()
    controller.add_sub_controller("Sub", sub)
    controller.set_id("X")

    api, _, _ = controller.create_api_and_tasks()

    assert api.path == ["X"]
    assert api.sub_apis["Sub"].path == ["X", "Sub"]


def _api_with_id(controller_class: type[Controller], id: str):
    controller = controller_class()
    controller.set_id(id)
    api, _, _ = controller.create_api_and_tasks()
    return api


def test_rest_transport_routes_two_controllers_by_id():
    api1 = _api_with_id(_OneAttrController, "alpha")
    api2 = _api_with_id(_OtherAttrController, "beta")

    loop = asyncio.new_event_loop()
    try:
        transport = RestTransport()
        transport.connect([api1, api2], loop)

        with TestClient(transport._server._app) as client:
            assert client.get("/alpha/foo").status_code == 200
            assert client.get("/beta/bar").status_code == 200
    finally:
        loop.close()


def test_rest_transport_rejects_illegal_id_at_connect():
    api = _api_with_id(_OneAttrController, "bad/id")

    loop = asyncio.new_event_loop()
    try:
        transport = RestTransport()
        with pytest.raises(ValueError, match="bad/id"):
            transport.connect([api], loop)
    finally:
        loop.close()


class _LifecycleController(Controller):
    """Records lifecycle hook calls for end-to-end assertions."""

    foo = AttrR(Int())

    def __init__(self):
        super().__init__()
        self.connected = False
        self.initialised = False
        self.post_initialised = False

    async def initialise(self):
        self.initialised = True

    def post_initialise(self):
        self.post_initialised = True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


class _OtherLifecycleController(_LifecycleController):
    bar = AttrR(Int())


@pytest.mark.asyncio
async def test_fastcs_serves_two_controllers_end_to_end(mocker: MockerFixture):
    """FastCS.serve drives lifecycle on every controller and routes REST traffic
    per-id; combined OpenAPI describes both prefixes."""
    a = _LifecycleController()
    a.set_id("alpha")
    b = _OtherLifecycleController()
    b.set_id("beta")

    transport = RestTransport()
    # Stop RestTransport from binding to a real port; we exercise the FastAPI
    # app directly through TestClient.
    mocker.patch.object(RestTransport, "serve", new=lambda self: asyncio.sleep(3600))

    fastcs = FastCS([a, b], [transport], asyncio.get_event_loop())
    task = asyncio.create_task(fastcs.serve(interactive=False))
    try:
        await asyncio.sleep(0.1)

        for controller in (a, b):
            assert controller.initialised
            assert controller.post_initialised
            assert controller.connected

        with TestClient(transport._server._app) as client:
            assert client.get("/alpha/foo").status_code == 200
            assert client.get("/beta/foo").status_code == 200
            assert client.get("/beta/bar").status_code == 200

            openapi = client.get("/openapi.json").json()
            paths = set(openapi["paths"])
            assert "/alpha/foo" in paths
            assert "/beta/foo" in paths
            assert "/beta/bar" in paths
    finally:
        task.cancel()
        await asyncio.sleep(0.1)

    for controller in (a, b):
        assert not controller.connected
