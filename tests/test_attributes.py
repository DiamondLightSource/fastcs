import asyncio
from functools import partial
from typing import assert_type

import numpy as np
import numpy.typing as npt
import pytest
from pytest_mock import MockerFixture

from fastcs.attributes import AttrR, AttrRW, AttrW, NotPolled, Polled, Update
from fastcs.controllers import Controller
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    Array1D,
    Limits,
    Meta,
    NumericLimits,
    Table,
)
from fastcs.util import ONCE


def test_attribute_access_mode():
    """Test that attributes have the correct access_mode property."""
    attr_r = AttrR(str)
    assert attr_r.access_mode == "r"

    attr_w = AttrW(str)
    assert attr_w.access_mode == "w"

    attr_rw = AttrRW(str)
    assert attr_rw.access_mode == "rw"


def test_attr_r():
    attr = AttrR(str, group="test group")

    assert not attr.has_getter()
    assert attr.poll_period is None
    assert attr.dtype is str
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
    assert attr.dtype is float


def test_datatype_inferred_from_setter_annotation():
    async def set_value(value: int) -> None:
        pass

    attr = AttrW(setter=set_value)
    assert_type(attr, AttrW[int])
    assert attr.dtype is int

    async def set_float(value: float) -> None:
        pass

    attr_rw = AttrRW(setter=set_float)
    assert_type(attr_rw, AttrRW[float])
    assert attr_rw.dtype is float


def test_datatype_required_when_not_inferable():
    expected_message = "datatype must be given explicitly"

    with pytest.raises(ValueError, match=expected_message):
        AttrR()

    with pytest.raises(ValueError, match=expected_message):
        AttrW()

    with pytest.raises(ValueError, match=expected_message):
        AttrRW()


@pytest.mark.asyncio
async def test_attr_update():
    attr = AttrRW(int)

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

    attr = AttrR(int, getter=do_update)
    assert attr.has_getter()

    value = await attr.poll()
    assert value == 5
    assert attr.readback == 5


@pytest.mark.asyncio
async def test_poll_unwraps_update_wrapper():
    async def do_update():
        return Update(9, timestamp=123.0)

    attr = AttrR(int, getter=do_update)
    value = await attr.poll()
    assert value == 9
    assert attr.readback == 9


@pytest.mark.asyncio
async def test_poll_with_no_getter_raises():
    attr = AttrR(int)

    with pytest.raises(RuntimeError, match="has no getter"):
        await attr.poll()


@pytest.mark.asyncio
async def test_poll_exception_propagates():
    async def do_update():
        raise ValueError("do_update failed")

    attr = AttrR(int, getter=do_update)

    with pytest.raises(ValueError, match="do_update failed"):
        await attr.poll()


def test_poll_period_comes_from_the_getter():
    async def do_update():
        return 1

    # A bare getter is read once, when the controller connects.
    attr = AttrR(int, getter=do_update)
    assert attr.poll_period == ONCE

    # Wrapping it in Polled schedules it instead.
    attr_explicit = AttrR(int, getter=Polled(do_update, period=0.5))
    assert attr_explicit.poll_period == 0.5

    # NotPolled is never scheduled - on-demand poll() only.
    attr_on_demand = AttrR(int, getter=NotPolled(do_update))
    assert attr_on_demand.poll_period is None
    assert attr_on_demand.has_getter()

    attr_no_getter = AttrR(int)
    assert attr_no_getter.poll_period is None


@pytest.mark.parametrize("period", [0, -1, float("nan")])
def test_polled_period_must_be_positive(period: float):
    async def do_update():
        return 1

    # rejected whether the getter is given directly or bound by decoration
    with pytest.raises(ValueError, match="Polled period must be positive"):
        Polled(do_update, period=period)
    with pytest.raises(ValueError, match="Polled period must be positive"):
        Polled(period=period)


def test_datatype_inferred_from_only_the_annotation_it_needs():
    # String annotations, as under ``from __future__ import annotations``. Neither
    # unresolvable name is the one the datatype comes from, so inference succeeds.
    async def set_value(value: "int") -> "OnlyImportedForTypeChecking":  # noqa: F821  # pyright: ignore[reportUndefinedVariable]
        pass

    async def get_value(extra: "AlsoUnresolvable" = None) -> "float":  # noqa: F821  # pyright: ignore[reportUndefinedVariable]
        return 1.5

    assert AttrW(setter=set_value).dtype is int
    assert AttrR(getter=get_value).dtype is float

    # the datatype's own annotation failing to resolve still needs a datatype
    async def set_unresolvable(value: "Unresolvable") -> None:  # noqa: F821  # pyright: ignore[reportUndefinedVariable]
        pass

    with pytest.raises(ValueError, match="datatype must be given explicitly"):
        AttrW(setter=set_unresolvable)


@pytest.mark.asyncio
async def test_wait_for_predicate(mocker: MockerFixture):
    attr = AttrR(int, initial_value=0)

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
    attr = AttrR(int, initial_value=0)

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

    attr_r = AttrR(str)
    attr_r.add_readback_callback(partial(update_ui, key="state"), always=False)
    await attr_r.update(device["state"])
    assert ui["state"] == "Idle"
    # Update with new value triggers callback
    assert ui["update_count"] == 1
    await attr_r.update(device["state"])
    # Identical update does not trigger callback as always=False
    assert ui["update_count"] == 1

    attr_rw = AttrRW(int, setter=partial(send, key="number"))
    attr_rw.add_readback_callback(partial(update_ui, key="number"))
    await attr_rw.set(2)
    assert device["number"] == 2
    assert ui["number"] == 2


@pytest.mark.asyncio
async def test_soft_attribute_self_wires():
    """With no getter/setter, AttrRW.set() pushes straight to readback."""
    attr = AttrRW(int)
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

    attr = AttrRW(int, setter=setter)

    await attr.set(10)
    assert accepted["value"] == 10
    assert attr.setpoint == 11
    assert attr.readback == 11


@pytest.mark.asyncio
async def test_setter_with_no_return_leaves_readback_untouched():
    async def setter(value):
        return None

    attr = AttrRW(int, setter=setter)

    await attr.set(5)
    assert attr.setpoint == 5
    assert attr.readback == 0  # unchanged - no getter/poll has happened


@pytest.mark.asyncio
async def test_attrw_setter_return_value_updates_setpoint_cache():
    async def setter(value):
        return value + 1

    attr = AttrW(int, setter=setter)

    await attr.set(5)
    assert attr.setpoint == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("attr_cls", [AttrW, AttrRW])
async def test_setpoint_is_published_before_the_setter_runs(attr_cls):
    published: list[int] = []
    seen_by_setter: list[int] = []

    async def on_setpoint(value: int):
        published.append(value)

    async def setter(value: int):
        seen_by_setter.append(attr.setpoint)
        return value + 1  # device clamps the request

    attr = attr_cls(int, setter=setter)
    attr.add_setpoint_callback(on_setpoint)

    await attr.set(5)

    # the request is visible while the setter runs, then the clamp replaces it
    assert seen_by_setter == [5]
    assert published == [5, 6]
    assert attr.setpoint == 6


@pytest.mark.asyncio
async def test_attrrw_set_setter_exception_is_raised():
    async def do_set(value):
        raise ValueError("do_set failed")

    attr = AttrRW(int, setter=do_set)

    # the setpoint keeps the requested value, as an EPICS record does after a
    # failed write; the readback is untouched
    with pytest.raises(ValueError, match="do_set failed"):
        await attr.set(5)
    assert attr.setpoint == 5
    assert attr.readback == 0


@pytest.mark.asyncio
async def test_set_setter_exception_is_logged_and_raised(mocker: MockerFixture):
    async def do_set(value):
        raise ValueError("do_set failed")

    attr = AttrW(int, setter=do_set)
    mock_logger = mocker.patch("fastcs.attributes.attr_w.logger")

    # exception is raised so transports can set alarms, and the setpoint keeps
    # the requested value
    with pytest.raises(ValueError, match="do_set failed"):
        await attr.set(5)
    assert attr.setpoint == 5

    # the setter's exception is the one logged, at error level
    logged_exception = mock_logger.opt.call_args.kwargs["exception"]
    assert isinstance(logged_exception, ValueError)
    assert str(logged_exception) == "do_set failed"
    mock_logger.opt.return_value.error.assert_called_once_with(
        "Set failed", attribute=attr, setpoint=5
    )


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
            dtype_mapping = {"int": int, "float": float}
            example_introspection_response = await self._connection.get(
                "config/introspect_api"
            )
            assert isinstance(example_introspection_response, list)
            for parameter_response in example_introspection_response:
                try:
                    ro = parameter_response["read_only"]
                    name = parameter_response["name"]
                    uri = f"{parameter_response['subsystem']}/{name}"
                    datatype = dtype_mapping[parameter_response["dtype"]]
                    limits = NumericLimits(
                        control=Limits(
                            low=parameter_response.get("min", None),
                            high=parameter_response.get("max", None),
                        )
                    )

                    async def getter(uri=uri) -> int | float:
                        return await self._connection.get(uri)  # type: ignore[return-value]

                    if ro:
                        attr = AttrR(
                            datatype,
                            getter=getter,
                            initial_value=parameter_response.get("value", None),
                            limits=limits,
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
                            limits=limits,
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


@pytest.mark.asyncio
async def test_attribute_metadata():
    attr = AttrRW(float, precision=3, units="degC", description="the temperature")
    seen: list[Meta] = []
    attr.add_update_meta_callback(seen.append)

    assert attr.dtype is float
    assert attr.meta == {
        "precision": 3,
        "units": "degC",
        "description": "the temperature",
    }
    assert attr.description == "the temperature"

    # A field set to None is dropped, as it is by the constructor
    attr.update_meta(Meta(units="mm", precision=None))  # pyright: ignore[reportArgumentType]

    assert attr.meta == {"units": "mm"}
    assert seen == [{"units": "mm"}]  # Updating metadata runs callbacks

    controlled = AttrRW(int, limits=NumericLimits(control=Limits(0, 10)))
    await controlled.set(5)
    assert controlled.readback == 5

    # Only the control range constrains a value; the rest are served, not enforced
    alarmed = AttrR(float, limits=NumericLimits(alarm=Limits(0.0, 10.0)))
    await alarmed.update(15.0)
    assert alarmed.readback == 15.0


def test_array_attribute():
    subscripted = AttrR(Array1D[np.int32], shape=(4,))
    assert subscripted.dtype is np.ndarray
    assert subscripted.meta.get("array_dtype") is np.int32
    assert np.array_equal(subscripted.readback, np.zeros(4, dtype=np.int32))  # Default

    unshaped = AttrR(npt.NDArray[np.int32])
    assert unshaped.meta.get("array_dtype") is np.int32
    assert unshaped.readback.dtype == np.int32
    assert unshaped.readback.shape == DEFAULT_ARRAY_SHAPE


def test_metadata_the_datatype_has_no_use_for_is_rejected():
    # Also a static error - the constructor overloads unpack StrMeta for a str
    # attribute - but the runtime check is what catches metadata arriving from a
    # source the type checker never saw.
    with pytest.raises(TypeError, match="'precision' is not valid metadata for str"):
        AttrR(str, precision=3)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_update_meta_rejects_metadata_the_datatype_has_no_use_for():
    attr = AttrR(int)

    with pytest.raises(TypeError, match="'length' is not valid metadata for int"):
        attr.update_meta(Meta(length=4))


@pytest.mark.parametrize(
    "attr",
    [
        pytest.param(
            AttrR(np.ndarray, array_dtype=np.int32, shape=(10, 10)),
            id="missing-array-dtype",
        ),
        pytest.param(
            AttrR(Table, structured_dtype=[("index", np.int32)]),
            id="missing-structured-dtype",
        ),
    ],
)
def test_update_meta_rejects_metadata_if_missing_required_fields(attr: AttrR):
    with pytest.raises(
        TypeError, match="An array attribute needs array_dtype or structured_dtype"
    ):
        attr.update_meta(Meta(description="An update"))


@pytest.mark.parametrize(
    "attr, meta",
    [
        pytest.param(
            AttrR(np.ndarray, array_dtype=np.int32, shape=(10, 10)),
            Meta(structured_dtype=[("index", np.int32)]),
            id="switch-structured-dtype",
        ),
        pytest.param(
            AttrR(Table, structured_dtype=[("index", np.int32)]),
            Meta(array_dtype=np.int32),
            id="switch-array-dtype",
        ),
    ],
)
def test_update_meta_rejects_metadata_if_switching_array_and_table(
    attr: AttrR, meta: Meta
):
    with pytest.raises(
        TypeError,
        match="update_meta cannot change an attribute between array and table",
    ):
        attr.update_meta(meta)


def test_rejected_update_meta_leaves_the_metadata_unchanged():
    attr = AttrR(Table, structured_dtype=[("index", np.int32)])
    meta = attr.meta

    with pytest.raises(TypeError):
        attr.update_meta(Meta(description="An update"))

    assert attr.meta == meta


def test_array_element_type_is_not_given_twice():
    with pytest.raises(TypeError, match="already given by the datatype subscript"):
        AttrR(Array1D[np.int32], array_dtype=np.int64)


def test_a_table_needs_its_columns():
    with pytest.raises(TypeError, match="Table attribute needs its columns"):
        AttrR(Table)


def test_structured_dtype_needs_a_table_datatype():
    with pytest.raises(TypeError, match="only valid for a Table attribute"):
        # Statically an error, as structured_dtype belongs to TableMeta.
        # Checked at runtime as well.
        AttrR(  # pyright: ignore[reportCallIssue]
            Array1D[np.int32],  # pyright: ignore[reportArgumentType]
            structured_dtype=[("a", np.int32)],
        )


@pytest.mark.asyncio
async def test_setpoint_outside_control_limits_is_rejected():
    attr = AttrRW(int, limits=NumericLimits(control=Limits(0, 10)))

    with pytest.raises(ValueError, match="greater than maximum 10"):
        await attr.set(15)


@pytest.mark.asyncio
async def test_setpoint_outside_control_limits_never_reaches_the_setter(
    mocker: MockerFixture,
):
    setter = mocker.AsyncMock()
    attr = AttrW(int, setter=setter, limits=NumericLimits(control=Limits(0, 10)))

    with pytest.raises(ValueError, match="greater than maximum 10"):
        await attr.set(15)
    setter.assert_not_called()
    assert attr.setpoint == 0


@pytest.mark.asyncio
async def test_readback_outside_control_limits_is_accepted():
    attr = AttrR(int, limits=NumericLimits(control=Limits(0, 10)))

    await attr.update(15)
    assert attr.readback == 15
