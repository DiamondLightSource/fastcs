"""Tests for the multi-controller foundation slice (#353).

These tests exercise the public Controller.id lifecycle and
multi-controller REST routing through RestTransport.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from fastcs.attributes import AttrR
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
