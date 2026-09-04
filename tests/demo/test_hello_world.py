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
    assert controller.uptime.description == "Seconds since the controller was constructed."


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
    assert await controller.message.poll() == "Hello, world!"

    await controller.greeting.set("Goodbye")

    assert controller.greeting.setpoint == "Goodbye"
    assert await controller.greeting.poll() == "Goodbye"
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


def test_each_instance_gets_its_own_attributes():
    one, two = HelloWorldController(), HelloWorldController()

    assert one.greeting is not two.greeting


@pytest.mark.asyncio
async def test_setting_one_instance_leaves_the_other_alone():
    one, two = HelloWorldController(), HelloWorldController()

    await one.greeting.set("Goodbye")

    assert await one.message.poll() == "Goodbye, world!"
    assert await two.message.poll() == "Hello, world!"
