import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Generic, TypeVar

import numpy as np
import numpy.typing as npt
import pytest
from pytest_mock import MockerFixture

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    Array1D,
    Limits,
    Meta,
    NumericLimits,
    Table,
)


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

    with pytest.raises(RuntimeError):
        _ = attr.io_ref

    assert not attr.has_io_ref()
    assert attr.dtype == str
    assert attr.group == "test group"
    assert attr.name == ""
    assert attr.path == []

    attr.set_name("test_name")
    attr.set_path(["test_path"])

    assert attr.name == "test_name"
    assert attr.path == ["test_path"]

    with pytest.raises(RuntimeError):
        attr.set_name("test_name")
    with pytest.raises(RuntimeError):
        attr.set_path(["test_path"])

    assert attr.get() == ""


@pytest.mark.asyncio
async def test_attr_update(mocker: MockerFixture):
    attr = AttrRW(int)

    await attr.update(42)
    assert attr.get() == 42

    await attr.update("100")  # type: ignore
    assert attr.get() == 100

    with pytest.raises(ValueError, match="Failed to cast"):
        await attr.update("not_an_int")  # type: ignore

    attr = AttrRW(int)
    sync_setpoint_mock = mocker.AsyncMock()
    attr.add_sync_setpoint_callback(sync_setpoint_mock)

    await attr.update("200")  # type: ignore
    assert attr.get() == 200
    sync_setpoint_mock.assert_called_once_with(200)

    sync_setpoint_mock.reset_mock()
    await attr.update(20)
    assert attr.get() == 20
    sync_setpoint_mock.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_predicate(mocker: MockerFixture):
    attr = AttrR(int, initial_value=0)

    async def update(attr: AttrR):
        while True:
            await asyncio.sleep(0.1)
            await attr.update(attr.get() + 3)  # 3, 6, 9, 12 != 10

    asyncio.create_task(update(attr))

    # We won't see exactly 10 so check for greater than
    def predicate(v: int) -> bool:
        return v > 10

    wait_mock = mocker.spy(asyncio, "wait_for")
    with pytest.raises(TimeoutError):
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
    with pytest.raises(TimeoutError):
        await attr.wait_for_value(10, timeout=0.2)

    await attr.wait_for_value(1, timeout=1)

    assert wait_mock.call_count == 2

    # Returns immediately without creating event if value already as expected
    await attr.wait_for_value(1, timeout=1)
    assert wait_mock.call_count == 2


@pytest.mark.asyncio
async def test_attributes():
    device = {"state": "Idle", "number": 1, "count": False}
    ui = {"state": "", "number": 0, "count": False, "update_count": 0}

    async def update_ui(value, key):
        ui[key] = value
        ui["update_count"] += 1

    async def send(_attr, value, key):
        device[key] = value

    async def device_add():
        device["number"] += 1

    attr_r = AttrR(str)
    attr_r.add_on_update_callback(partial(update_ui, key="state"), always=False)
    await attr_r.update(device["state"])
    assert ui["state"] == "Idle"
    # Update with new value triggers callback
    assert ui["update_count"] == 1
    await attr_r.update(device["state"])
    # Identical update does not trigger callback as always=False
    assert ui["update_count"] == 1

    attr_rw = AttrRW(int)
    attr_rw._on_put_callback = partial(send, key="number")
    attr_rw.add_sync_setpoint_callback(partial(update_ui, key="number"))
    await attr_rw.put(2, sync_setpoint=True)
    assert device["number"] == 2
    assert ui["number"] == 2


@pytest.mark.asyncio
async def test_attribute_io():
    @dataclass
    class MyAttributeIORef(AttributeIORef):
        cool: int

    class MyAttributeIO(AttributeIO[int, MyAttributeIORef]):
        async def update(self, attr: AttrR[int, MyAttributeIORef]):
            print("I am updating", self.ref_type, attr.io_ref.cool)

    class MyController(Controller):
        my_attr = AttrR(int, io_ref=MyAttributeIORef(cool=5))
        your_attr = AttrR(int, io_ref=MyAttributeIORef(cool=10))

        def __init__(self):
            super().__init__(ios=[MyAttributeIO()])

    c = MyController()

    class ControllerNoIO(Controller):
        my_attr = AttrR(int, io_ref=MyAttributeIORef(cool=5))

    @dataclass
    class OtherAttributeIORef(AttributeIORef):
        not_cool: int

    class MissingIOController(Controller):
        my_attr = AttrR(int, io_ref=OtherAttributeIORef(not_cool=5))

    with pytest.raises(ValueError, match="does not have an AttributeIO to handle"):
        controller = MissingIOController()
        controller._connect_attribute_ios()

    await c.initialise()
    c._connect_attribute_ios()
    await c.my_attr.bind_update_callback()()


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


NumberT = TypeVar("NumberT", int, float)


@pytest.mark.asyncio()
async def test_dynamic_attribute_io_specification():
    @dataclass
    class DemoParameterAttributeIORef(AttributeIORef, Generic[NumberT]):
        name: str
        subsystem: str
        connection: DummyConnection

        @property
        def uri(self):
            return f"{self.subsystem}/{self.name}"

    class DemoParameterAttributeIO(AttributeIO[NumberT, DemoParameterAttributeIORef]):
        async def update(
            self,
            attr: AttrR[NumberT, DemoParameterAttributeIORef],
        ):
            value = await attr.io_ref.connection.get(attr.io_ref.uri)
            await attr.update(value)  # type: ignore

        async def send(
            self,
            attr: AttrW[NumberT, DemoParameterAttributeIORef],
            value: NumberT,
        ) -> None:
            await attr.io_ref.connection.set(attr.io_ref.uri, value)
            if isinstance(attr, AttrRW):
                await self.update(attr)

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
                    ref = DemoParameterAttributeIORef(
                        name=parameter_response["name"],
                        subsystem=parameter_response["subsystem"],
                        connection=self._connection,
                    )
                    attr_class = AttrR if ro else AttrRW
                    attr = attr_class(
                        dtype_mapping[parameter_response["dtype"]],
                        io_ref=ref,
                        initial_value=parameter_response.get("value", None),
                        limits=NumericLimits(
                            control=Limits(
                                low=parameter_response.get("min", None),
                                high=parameter_response.get("max", None),
                            )
                        ),
                    )

                    self.add_attribute(ref.name, attr)
                except Exception as e:
                    print(
                        "Exception constructing attribute from parameter response:",
                        parameter_response,
                        e,
                    )

    c = DemoParameterController(ios=[DemoParameterAttributeIO()])
    await c.initialise()
    c._connect_attribute_ios()
    await c.ro_int_parameter.bind_update_callback()()
    assert c.ro_int_parameter.get() == 10
    await c.ro_int_parameter.bind_update_callback()()
    assert c.ro_int_parameter.get() == 11

    await c.int_parameter.put(20)
    assert c.int_parameter.get() == 20


@pytest.mark.asyncio
async def test_attribute_no_io(mocker: MockerFixture):
    class MyController(Controller):
        no_ref = AttrRW(int)
        base_class_ref = AttrRW(int, io_ref=AttributeIORef())

    with pytest.raises(
        ValueError,
        match="MyController does not have an AttributeIO to handle AttributeIORef",
    ):
        c = MyController()
        c._connect_attribute_ios()

    class SimpleAttributeIO(AttributeIO[int]):
        async def update(self, attr):
            await attr.update(100)

    with pytest.raises(
        RuntimeError, match="More than one AttributeIO class handles AttributeIORef"
    ):
        MyController(ios=[SimpleAttributeIO(), SimpleAttributeIO()])

    # we need to explicitly pass an AttributeIO if we want to handle instances of
    # the AttributeIORef base class
    c = MyController(ios=[SimpleAttributeIO()])
    assert not c.no_ref.has_io_ref()
    assert c.base_class_ref.has_io_ref()

    await c.initialise()
    c._connect_attribute_ios()

    # There is a difference between providing an AttributeIO for the default
    # AttributeIORef class and not specifying the io_ref for an Attribute
    # default callbacks are not provided by AttributeIO subclasses

    sync_setpoint_mock = mocker.AsyncMock()
    c.no_ref.add_sync_setpoint_callback(sync_setpoint_mock)

    await c.no_ref.put(40)
    sync_setpoint_mock.assert_called_once_with(40)  # sync setpoint called on first set
    sync_setpoint_mock.reset_mock()
    await c.no_ref.put(41)  # sync setpoint callback not called without flag
    await c.no_ref.put(42, sync_setpoint=True)
    sync_setpoint_mock.assert_called_once_with(42)

    c2 = MyController(ios=[SimpleAttributeIO()])

    await c2.initialise()
    c2._connect_attribute_ios()

    assert c2.base_class_ref.get() == 0
    await c2.base_class_ref.bind_update_callback()()
    assert c2.base_class_ref.get() == 100


def test_add_update_callback_twice_raises():
    async def do_update(attr: AttrR[int]):
        pass

    attr = AttrRW(int)
    attr.set_update_callback(do_update)

    with pytest.raises(RuntimeError):
        attr.set_update_callback(do_update)


@pytest.mark.asyncio
async def test_bind_update():
    attr = AttrRW(int)

    with pytest.raises(RuntimeError):
        attr.bind_update_callback()

    async def do_update(attr: AttrR[int]):
        await attr.update(5)

    attr.set_update_callback(do_update)
    callback = attr.bind_update_callback()

    await callback()
    assert attr.get() == 5


@pytest.mark.asyncio
async def test_bind_update_exception():
    attr = AttrRW(int)

    async def do_update(attr: AttrR[int]):
        raise ValueError("do_update failed")

    attr.set_update_callback(do_update)

    callback = attr.bind_update_callback()

    with pytest.raises(ValueError):
        await callback()


@pytest.mark.asyncio
async def test_put():
    attr = AttrW(int)

    async def do_put(attr: AttrW[int], value: int):
        raise ValueError("do_put failed")

    async def do_sync_setpoint(setpoint: int):
        raise ValueError("do_sync_setpoint failed")

    attr.set_on_put_callback(do_put)
    attr.add_sync_setpoint_callback(do_sync_setpoint)

    with pytest.raises(ValueError, match="do_put failed"):
        await attr.put(5)

    with pytest.raises(RuntimeError, match="Attribute already has an on put callback"):
        attr.set_on_put_callback(do_put)


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
    await controlled.put(5)
    assert controlled.get() == 5

    # Only the control range constrains a value; the rest are served, not enforced
    alarmed = AttrR(float, limits=NumericLimits(alarm=Limits(0.0, 10.0)))
    await alarmed.update(15.0)
    assert alarmed.get() == 15.0


def test_array_attribute():
    subscripted = AttrR(Array1D[np.int32], shape=(4,))
    assert subscripted.dtype is np.ndarray
    assert subscripted.meta.get("array_dtype") is np.int32
    assert np.array_equal(subscripted.get(), np.zeros(4, dtype=np.int32))  # Default

    unshaped = AttrR(npt.NDArray[np.int32])
    assert unshaped.meta.get("array_dtype") is np.int32
    assert unshaped.get().dtype == np.int32
    assert unshaped.get().shape == DEFAULT_ARRAY_SHAPE


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
        await attr.put(15)


@pytest.mark.asyncio
async def test_readback_outside_control_limits_is_accepted():
    attr = AttrR(int, limits=NumericLimits(control=Limits(0, 10)))

    await attr.update(15)
    assert attr.get() == 15
