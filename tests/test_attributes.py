import asyncio
from functools import partial

import pytest
from pytest_mock import MockerFixture

from fastcs.attributes import AttrR, AttrRW, AttrW, NotPolled, Polled, Update
from fastcs.controllers import Controller
from fastcs.datatypes import Float, Int, String
from fastcs.util import ONCE


def test_attribute_access_mode():
    """Test that attributes have the correct access_mode property."""
    attr_r = AttrR(String())
    assert attr_r.access_mode == "r"

    attr_w = AttrW(String())
    assert attr_w.access_mode == "w"

    attr_rw = AttrRW(String())
    assert attr_rw.access_mode == "rw"


def test_attr_r():
    attr = AttrR(String(), group="test group")

    assert not attr.has_getter()
    assert attr.poll_period is None
    assert isinstance(attr.datatype, String)
    assert attr.dtype == str
    assert attr.group == "test group"
    assert attr.name == ""
    assert attr.path == []

    attr.set_name("test_name")
    attr.set_path(["test_path"])

    assert attr.name == "test_name"
    assert attr.path == ["test_path"]

    with pytest.raises(RuntimeError, match="already registered with a controller as"):
        attr.set_name("test_name")
    with pytest.raises(RuntimeError, match="already registered with a controller at"):
        attr.set_path(["test_path"])

    assert attr.readback == ""


def test_datatype_inferred_from_getter_annotation():
    async def get_value() -> float:
        return 1.5

    attr = AttrR(getter=get_value)
    assert isinstance(attr.datatype, Float)


def test_datatype_inferred_from_setter_annotation():
    async def set_value(value: int) -> None:
        pass

    attr = AttrW(setter=set_value)
    assert isinstance(attr.datatype, Int)


def test_datatype_required_when_not_inferable():
    with pytest.raises(ValueError, match="datatype must be given explicitly"):
        AttrR()

    with pytest.raises(ValueError, match="datatype must be given explicitly"):
        AttrW()

    with pytest.raises(ValueError, match="datatype must be given explicitly"):
        AttrRW()


@pytest.mark.asyncio
async def test_attr_update():
    attr = AttrRW(Int())

    await attr.update(42)
    assert attr.readback == 42

    await attr.update("100")  # type: ignore
    assert attr.readback == 100

    with pytest.raises(ValueError, match="Failed to cast"):
        await attr.update("not_an_int")  # type: ignore

    # update() also accepts an Update wrapper, unwrapping to just the value
    await attr.update(Update(7, timestamp=123.0))
    assert attr.readback == 7


@pytest.mark.asyncio
async def test_poll():
    async def do_update():
        return 5

    attr = AttrR(Int(), getter=do_update)
    assert attr.has_getter()

    value = await attr.poll()
    assert value == 5
    assert attr.readback == 5


@pytest.mark.asyncio
async def test_poll_unwraps_update_wrapper():
    async def do_update():
        return Update(9, timestamp=123.0)

    attr = AttrR(Int(), getter=do_update)
    value = await attr.poll()
    assert value == 9
    assert attr.readback == 9


@pytest.mark.asyncio
async def test_poll_with_no_getter_raises():
    attr = AttrR(Int())

    with pytest.raises(RuntimeError, match="has no getter"):
        await attr.poll()


@pytest.mark.asyncio
async def test_poll_exception_propagates():
    async def do_update():
        raise ValueError("do_update failed")

    attr = AttrR(Int(), getter=do_update)

    with pytest.raises(ValueError, match="do_update failed"):
        await attr.poll()


def test_poll_period_comes_from_the_getter():
    async def do_update():
        return 1

    # A bare getter is read once, when the controller connects.
    attr = AttrR(Int(), getter=do_update)
    assert attr.poll_period == ONCE

    # Wrapping it in Polled schedules it instead.
    attr_explicit = AttrR(Int(), getter=Polled(do_update, period=0.5))
    assert attr_explicit.poll_period == 0.5

    # NotPolled is never scheduled - on-demand poll() only.
    attr_on_demand = AttrR(Int(), getter=NotPolled(do_update))
    assert attr_on_demand.poll_period is None
    assert attr_on_demand.has_getter()

    attr_no_getter = AttrR(Int())
    assert attr_no_getter.poll_period is None


@pytest.mark.asyncio
async def test_wait_for_predicate(mocker: MockerFixture):
    attr = AttrR(Int(), initial_value=0)

    async def update(attr: AttrR):
        while True:
            await asyncio.sleep(0.1)
            await attr.update(attr.readback + 3)  # 3, 6, 9, 12 != 10

    asyncio.create_task(update(attr))

    # We won't see exactly 10 so check for greater than
    def predicate(v: int) -> bool:
        return v > 10

    wait_mock = mocker.spy(asyncio, "wait_for")
    with pytest.raises(TimeoutError, match="Timeout waiting 0.2s for .* predicate"):
        await attr.wait_for_predicate(predicate, timeout=0.2)

    await attr.wait_for_predicate(predicate, timeout=1)

    assert wait_mock.call_count == 2

    # Returns immediately without creating event if value already as expected
    await attr.wait_for_predicate(predicate, timeout=1)
    assert wait_mock.call_count == 2


@pytest.mark.asyncio
async def test_wait_for_value(mocker: MockerFixture):
    attr = AttrR(Int(), initial_value=0)

    async def update(attr: AttrR):
        await asyncio.sleep(0.5)
        await attr.update(1)

    asyncio.create_task(update(attr))

    wait_mock = mocker.spy(asyncio, "wait_for")
    with pytest.raises(TimeoutError, match="Timeout waiting 0.2s for .* value 10"):
        await attr.wait_for_value(10, timeout=0.2)

    await attr.wait_for_value(1, timeout=1)

    assert wait_mock.call_count == 2

    # Returns immediately without creating event if value already as expected
    await attr.wait_for_value(1, timeout=1)
    assert wait_mock.call_count == 2


@pytest.mark.asyncio
async def test_attributes():
    device = {"state": "Idle", "number": 1, "count": False}
    ui = {"state": "", "number": 0, "update_count": 0}

    async def update_ui(value, key):
        ui[key] = value
        ui["update_count"] += 1

    async def send(value, key):
        device[key] = value
        return value  # accepted value echoes straight back to the readback

    attr_r = AttrR(String())
    attr_r.add_readback_callback(partial(update_ui, key="state"), always=False)
    await attr_r.update(device["state"])
    assert ui["state"] == "Idle"
    # Update with new value triggers callback
    assert ui["update_count"] == 1
    await attr_r.update(device["state"])
    # Identical update does not trigger callback as always=False
    assert ui["update_count"] == 1

    attr_rw = AttrRW(Int(), setter=partial(send, key="number"))
    attr_rw.add_readback_callback(partial(update_ui, key="number"))
    await attr_rw.set(2)
    assert device["number"] == 2
    assert ui["number"] == 2


@pytest.mark.asyncio
async def test_soft_attribute_self_wires():
    """With no getter/setter, AttrRW.set() pushes straight to readback."""
    attr = AttrRW(Int())
    assert not attr.has_getter()
    assert not attr.has_setter()

    await attr.set(40)
    assert attr.setpoint == 40
    assert attr.readback == 40


@pytest.mark.asyncio
async def test_setter_return_value_updates_readback():
    accepted = {}

    async def setter(value):
        accepted["value"] = value
        return value + 1  # device clamps/accepts a different value

    attr = AttrRW(Int(), setter=setter)

    await attr.set(10)
    assert accepted["value"] == 10
    assert attr.setpoint == 11
    assert attr.readback == 11


@pytest.mark.asyncio
async def test_setter_with_no_return_leaves_readback_untouched():
    async def setter(value):
        return None

    attr = AttrRW(Int(), setter=setter)

    await attr.set(5)
    assert attr.setpoint == 5
    assert attr.readback == 0  # unchanged - no getter/poll has happened


@pytest.mark.asyncio
async def test_attrw_setter_return_value_updates_setpoint_cache():
    async def setter(value):
        return value + 1

    attr = AttrW(Int(), setter=setter)

    await attr.set(5)
    assert attr.setpoint == 6


@pytest.mark.asyncio
async def test_set_setter_exception_is_caught_and_logged():
    async def do_set(value):
        raise ValueError("do_set failed")

    attr = AttrW(Int(), setter=do_set)

    # exception is caught and logged, not raised
    await attr.set(5)
    assert attr.setpoint == 5


class DummyConnection:
    def __init__(self):
        self._connected = False
        self._int_value = 5
        self._ro_int_value = 10
        self._float_value = 7.5

    async def connect(self):
        self._connected = True

    async def get(self, uri: str):
        if not self._connected:
            raise TimeoutError("No response from DummyConnection")
        if uri == "config/introspect_api":
            return [
                {
                    "name": "int_parameter",
                    "subsystem": "status",
                    "dtype": "int",
                    "min": 0,
                    "max": 100,
                    "value": self._int_value,
                    "read_only": False,
                },
                {
                    "name": "ro_int_parameter",
                    "subsystem": "status",
                    "dtype": "int",
                    "value": self._ro_int_value,
                    "read_only": True,
                },
                {
                    "name": "float_parameter",
                    "subsystem": "status",
                    "dtype": "float",
                    "max": 1000.0,
                    "value": self._float_value,
                    "read_only": False,
                },
            ]

        # increment after getting
        elif uri == "status/int_parameter":
            value = self._int_value
            self._int_value += 1
        elif uri == "status/ro_int_parameter":
            value = self._ro_int_value
            self._ro_int_value += 1
        elif uri == "status/float_parameter":
            value = self._float_value
            self._float_value += 1
        else:
            raise RuntimeError()
        return value

    async def set(self, uri: str, value: float | int):
        if uri == "status/int_parameter":
            self._int_value = value
        elif uri == "status/ro_int_parameter":
            # don't update read only parameter
            pass
        elif uri == "status/float_parameter":
            self._float_value = value


@pytest.mark.asyncio()
async def test_dynamic_attribute_getter_setter_specification():
    class DemoParameterController(Controller):
        ro_int_parameter: AttrR
        int_parameter: AttrRW
        float_parameter: AttrRW  # hint to satisfy pyright

        async def initialise(self):
            self._connection = DummyConnection()
            await self._connection.connect()
            dtype_mapping = {"int": Int, "float": Float}
            example_introspection_response = await self._connection.get(
                "config/introspect_api"
            )
            assert isinstance(example_introspection_response, list)
            for parameter_response in example_introspection_response:
                try:
                    ro = parameter_response["read_only"]
                    name = parameter_response["name"]
                    uri = f"{parameter_response['subsystem']}/{name}"
                    datatype = dtype_mapping[parameter_response["dtype"]](
                        min=parameter_response.get("min", None),
                        max=parameter_response.get("max", None),
                    )

                    async def getter(uri=uri) -> int | float:
                        return await self._connection.get(uri)  # type: ignore[return-value]

                    if ro:
                        attr = AttrR(
                            datatype,
                            getter=getter,
                            initial_value=parameter_response.get("value", None),
                        )
                    else:

                        async def setter(value, uri=uri):
                            await self._connection.set(uri, value)
                            return value

                        attr = AttrRW(
                            datatype,
                            getter=getter,
                            setter=setter,
                            initial_value=parameter_response.get("value", None),
                        )

                    self.add_attribute(name, attr)
                except Exception as e:
                    print(
                        "Exception constructing attribute from parameter response:",
                        parameter_response,
                        e,
                    )

    c = DemoParameterController()
    await c.initialise()

    assert await c.ro_int_parameter.poll() == 10
    assert await c.ro_int_parameter.poll() == 11

    await c.int_parameter.set(20)
    assert c.int_parameter.readback == 20
