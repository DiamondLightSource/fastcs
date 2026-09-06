import pytest

from fastcs.attributes import AttrR, AttrRW
from fastcs.demo.hello_world import HelloWorldController
from fastcs.util import ONCE


@pytest.fixture
def controller() -> HelloWorldController:
    return HelloWorldController()


def test_greeting_is_read_write(controller: HelloWorldController):
    assert isinstance(controller.greeting, AttrRW)
    assert controller.greeting.dtype is str


def test_derived_attributes_are_read_only(controller: HelloWorldController):
    assert isinstance(controller.message, AttrR)
    assert not isinstance(controller.message, AttrRW)
    assert isinstance(controller.uptime, AttrR)
    assert not isinstance(controller.uptime, AttrRW)


def test_docstrings_become_descriptions(controller: HelloWorldController):
    assert controller.greeting.description == "The word to greet with."
    assert controller.message.description == "The greeting as it currently reads."
    assert (
        controller.uptime.description == "Seconds since the controller was constructed."
    )


def test_schedules(controller: HelloWorldController):
    # A bare `@attr` is read once, on connect; the derived values are polled.
    assert controller.greeting.poll_period == ONCE
    assert controller.message.poll_period == 0.2
    assert controller.uptime.poll_period == 0.2


def test_decorator_keywords_are_metadata(controller: HelloWorldController):
    assert controller.uptime.dtype is float
    assert controller.uptime.meta == {
        "units": "s",
        "precision": 1,
        "description": "Seconds since the controller was constructed.",
    }


@pytest.mark.asyncio
async def test_message_follows_the_greeting(controller: HelloWorldController):
    greeting = controller.greeting
    assert isinstance(greeting, AttrRW)
    assert await controller.message.poll() == "Hello, world!"

    await greeting.set("Goodbye")

    assert greeting.setpoint == "Goodbye"
    assert await greeting.poll() == "Goodbye"
    assert await controller.message.poll() == "Goodbye, world!"


@pytest.mark.asyncio
async def test_subject_is_a_constructor_argument():
    controller = HelloWorldController("beamline")

    assert await controller.message.poll() == "Hello, beamline!"


@pytest.mark.asyncio
async def test_uptime_advances(controller: HelloWorldController):
    first = await controller.uptime.poll()
    second = await controller.uptime.poll()

    assert second >= first


@pytest.mark.asyncio
async def test_the_setter_is_also_an_ordinary_method(controller: HelloWorldController):
    await controller.set_greeting("Goodbye")

    assert await controller.message.poll() == "Goodbye, world!"
