import asyncio
from enum import Enum

import numpy as np
import pytest

from fastcs.attributes import (
    AttrR,
    AttrRW,
    NotPolled,
    Polled,
    UnboundAttr,
    Update,
    attr,
)
from fastcs.controllers import Controller
from fastcs.datatypes import Array1D, Limits, NumericLimits
from fastcs.util import ONCE


class State(Enum):
    IDLE = "idle"
    BUSY = "busy"


class PowerSupply(Controller):
    """A controller declaring its attributes with ``@attr``."""

    def __init__(self) -> None:
        super().__init__()

        self.sent: list[float] = []
        self._voltage = 1.5

    @attr(Polled(period=0.5), units="V", precision=3)
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        """Output voltage.

        The rest of the docstring says more than a description should.
        """
        return self._voltage

    @voltage.setter
    async def voltage(self, value: float) -> None:
        self.sent.append(value)
        self._voltage = value

    @attr
    async def serial(self) -> str:
        """Serial number."""
        return "PSU-1"

    @attr(NotPolled(), group="Config")
    async def retries(self) -> int:
        return 3


def test_getter_only_is_read_only():
    controller = PowerSupply()

    assert isinstance(controller.serial, AttrR)
    assert not isinstance(controller.serial, AttrRW)
    assert controller.serial.dtype is str
    assert controller.serial.access_mode == "r"


def test_getter_and_setter_is_read_write():
    controller = PowerSupply()

    assert isinstance(controller.voltage, AttrRW)
    assert controller.voltage.dtype is float
    assert controller.voltage.access_mode == "rw"


def test_attributes_are_registered_with_the_controller():
    controller = PowerSupply()

    assert list(controller.attributes) == ["voltage", "serial", "retries"]
    assert controller.attributes["voltage"] is controller.voltage
    assert controller.voltage.name == "voltage"


def test_metadata_from_decorator():
    controller = PowerSupply()

    assert controller.voltage.meta == {
        "units": "V",
        "precision": 3,
        "description": "Output voltage.",
    }
    assert controller.retries.meta == {"group": "Config"}
    assert controller.retries.group == "Config"


def test_docstring_summary_becomes_the_description():
    controller = PowerSupply()

    # Only the first paragraph - a description is a one-line label.
    assert controller.voltage.description == "Output voltage."
    assert controller.serial.description == "Serial number."
    assert controller.retries.description is None


def test_explicit_description_wins_over_the_docstring():
    class Device(Controller):
        @attr(description="From the decorator")
        async def label(self) -> str:
            """From the docstring."""
            return "x"

    assert Device().label.description == "From the decorator"


def test_schedules():
    controller = PowerSupply()

    assert controller.voltage.poll_period == 0.5
    # A bare ``@attr`` means what a bare ``getter=`` means - read once, at connect.
    assert controller.serial.poll_period is ONCE
    assert controller.retries.poll_period is None
    assert controller.retries.has_getter()


@pytest.mark.asyncio
async def test_bound_getter_reads_from_its_own_instance():
    one, two = PowerSupply(), PowerSupply()
    two._voltage = 9.0

    assert await one.voltage.poll() == 1.5
    assert await two.voltage.poll() == 9.0


@pytest.mark.asyncio
async def test_bound_setter_writes_to_its_own_instance():
    one, two = PowerSupply(), PowerSupply()

    await one.voltage.set(2.5)

    assert one.sent == [2.5]
    assert one._voltage == 2.5
    assert two.sent == []
    assert two._voltage == 1.5


def test_each_instance_gets_a_fresh_attribute():
    one, two = PowerSupply(), PowerSupply()

    assert one.voltage is not two.voltage
    assert one.serial is not two.serial


def test_class_body_holds_the_declaration():
    assert isinstance(PowerSupply.voltage, UnboundAttr)
    assert PowerSupply.voltage.datatype is float
    assert PowerSupply.voltage.has_setter()
    assert not PowerSupply.serial.has_setter()
    assert "PowerSupply.voltage" in repr(PowerSupply.voltage)
    assert "access_mode='rw'" in repr(PowerSupply.voltage)


def test_datatype_inferred_from_the_return_annotation():
    class Device(Controller):
        @attr
        async def flag(self) -> bool:
            return True

        @attr
        async def state(self) -> State:
            return State.IDLE

        @attr(shape=(4,))
        async def trace(self) -> Array1D[np.int32]:
            return np.zeros(4, dtype=np.int32)

    controller = Device()

    assert controller.flag.dtype is bool
    assert controller.state.dtype is State
    assert controller.trace.dtype is np.ndarray
    assert controller.trace.meta == {"array_dtype": np.int32, "shape": (4,)}


@pytest.mark.asyncio
async def test_update_return_annotation_is_unwrapped():
    class Device(Controller):
        @attr
        async def temperature(self) -> Update[float]:
            return Update(readback=20.5, timestamp=1000.0)

    controller = Device()

    assert controller.temperature.dtype is float
    assert await controller.temperature.poll() == 20.5
    assert controller.temperature.timestamp == 1000.0


def test_metadata_is_validated_against_the_inferred_datatype():
    with pytest.raises(TypeError, match="'precision' is not valid metadata"):

        class Device(Controller):
            @attr(precision=3)
            async def label(self) -> str:
                return "x"

        Device()


def test_limits_metadata():
    class Device(Controller):
        @attr(limits=NumericLimits(control=Limits(0.0, 10.0)))
        async def setpoint(self) -> float:
            return 1.0

    assert Device().setpoint.meta.get("limits") == NumericLimits(
        control=Limits(0.0, 10.0)
    )


def test_matching_type_hint_is_satisfied_by_the_decorated_attribute():
    class Device(Controller):
        label: AttrR[str]  # pyright: ignore[reportRedeclaration]

        @attr
        async def label(self) -> str:
            return "x"

    controller = Device()
    controller.post_initialise()

    assert isinstance(controller.label, AttrR)


def test_type_hint_of_the_wrong_access_mode_raises():
    with pytest.raises(RuntimeError, match="does not match defined access mode"):

        class Device(Controller):
            label: AttrRW[str]  # pyright: ignore[reportRedeclaration]

            @attr
            async def label(self) -> str:
                return "x"

        Device()


def test_name_clash_with_an_attribute_added_later_raises():
    class Device(Controller):
        def __init__(self) -> None:
            super().__init__()

            self.label = AttrR(str)  # pyright: ignore[reportAttributeAccessIssue]

        @attr
        async def label(self) -> str:
            return "x"

    with pytest.raises(ValueError, match="Cannot add attribute") as exc_info:
        Device()

    assert "has existing attribute label" in str(exc_info.value.__cause__)


def test_getter_must_be_async():
    with pytest.raises(TypeError, match="getter .* must be an async function"):

        @attr  # pyright: ignore[reportArgumentType, reportCallIssue]
        def voltage(self) -> float:
            return 0.0


def test_getter_must_take_only_self():
    with pytest.raises(TypeError, match="getter .* must be a method taking self"):

        @attr()  # pyright: ignore[reportArgumentType]
        async def voltage(self, index: int) -> float:
            return 0.0


def test_getter_must_annotate_its_return_type():
    with pytest.raises(TypeError, match="must annotate the datatype"):

        @attr()
        async def voltage(self):
            return 0.0


def test_getter_must_return_a_supported_datatype():
    with pytest.raises(TypeError, match="must annotate the datatype"):

        @attr()  # pyright: ignore[reportArgumentType]
        async def voltage(self) -> list[int]:
            return []


def test_setter_must_be_async():
    @attr
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        return 0.0

    with pytest.raises(TypeError, match="setter .* must be an async function"):

        @voltage.setter  # pyright: ignore[reportArgumentType]
        def voltage(self, value: float) -> None:
            pass


def test_setter_must_take_a_value():
    @attr
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        return 0.0

    with pytest.raises(
        TypeError, match="setter .* must be a method taking self and the value to set"
    ):

        @voltage.setter  # pyright: ignore[reportArgumentType]
        async def voltage(self) -> None:
            pass


def test_setter_value_must_match_the_getter_datatype():
    @attr
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        return 0.0

    with pytest.raises(TypeError, match="takes a str, but its getter returns a float"):

        @voltage.setter  # pyright: ignore[reportArgumentType]
        async def voltage(self, value: str) -> None:
            pass


def test_setter_value_annotation_is_optional():
    @attr
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        return 0.0

    voltage = voltage.setter(_untyped_setter)

    assert voltage.has_setter()


async def _untyped_setter(self, value) -> None:
    pass


def test_only_one_setter():
    @attr
    async def voltage(self) -> float:  # pyright: ignore[reportRedeclaration]
        return 0.0

    @voltage.setter
    async def voltage(self, value: float) -> None:
        pass

    with pytest.raises(TypeError, match="already has a setter"):

        @voltage.setter
        async def voltage(self, value: float) -> None:
            pass


def test_setter_does_not_leak_onto_the_class_it_was_inherited_from():
    class Base(Controller):
        @attr
        async def voltage(self) -> float:
            return 0.0

    class Child(Base):
        @Base.voltage.setter  # pyright: ignore[reportArgumentType]
        async def voltage(self, value: float) -> None:
            pass

    assert not Base.voltage.has_setter()
    assert Child.voltage.has_setter()
    assert not isinstance(Base().voltage, AttrRW)
    assert isinstance(Child().voltage, AttrRW)


def test_schedule_must_not_already_have_a_getter():
    async def read() -> float:
        return 0.0

    with pytest.raises(TypeError, match="already has a getter"):

        @attr(Polled(read, period=0.1))
        async def voltage(self) -> float:
            return 0.0


@pytest.mark.asyncio
async def test_polled_attributes_are_scheduled():
    controller = PowerSupply()
    _, periodic, initial = controller.create_api_and_tasks()

    # ``serial`` is read once at connect; ``voltage`` is polled at 0.5s;
    # ``retries`` is never read on a schedule.
    assert len(initial) == 1
    assert len(periodic) == 1

    await asyncio.gather(*[coro() for coro in initial])

    assert controller.serial.readback == "PSU-1"
    assert controller.retries.readback == 0
