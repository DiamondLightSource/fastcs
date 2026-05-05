"""Tests for the multi-controller foundation slice (#353).

These tests exercise the public Controller.id lifecycle.
"""

import pytest

from fastcs.controllers import Controller


class _IdController(Controller):
    pass


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
