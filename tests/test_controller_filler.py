"""Tests for `ControllerFiller` - the declarative half of ADR 0013."""

import enum
from typing import Annotated

import numpy as np
import pytest

from fastcs.attributes import AttrR, AttrRW, AttrW, NotPolled
from fastcs.controllers import Controller, ControllerVector
from fastcs.datatypes import Array1D
from fastcs.methods import Command, Scan


class Colour(enum.Enum):
    RED = "red"


def test_every_attribute_datatype_can_be_declared():
    class Declared(Controller):
        flag: AttrR[bool]
        count: AttrRW[int]
        reading: AttrR[float]
        label: AttrRW[str]
        colour: AttrR[Colour]
        trace: AttrR[Array1D[np.int32]]

    controller = Declared()

    assert {name: attr.dtype for name, attr in controller.attributes.items()} == {
        "flag": bool,
        "count": int,
        "reading": float,
        "label": str,
        "colour": Colour,
        "trace": np.ndarray,
    }


def test_access_mode_comes_from_the_declared_class():
    class Declared(Controller):
        readable: AttrR[int]
        writable: AttrW[int]
        both: AttrRW[int]

    controller = Declared()

    assert controller.readable.access_mode == "r"
    assert controller.writable.access_mode == "w"
    assert controller.both.access_mode == "rw"


def test_a_controller_with_no_hints_at_all_is_fine():
    # `fastcs-PandABlocks`/`fastcs-secop`: the whole tree comes off the wire.
    class Dynamic(Controller):
        async def initialise(self) -> None:
            self.add_attribute("discovered", AttrR(int))

    controller = Dynamic()

    assert controller.attributes == {}
    controller.check_filled()


@pytest.mark.asyncio
async def test_attributes_added_without_a_hint_are_left_alone():
    class Dynamic(Controller):
        async def initialise(self) -> None:
            self.add_attribute("discovered", AttrR(int))

    controller = Dynamic()
    await controller.initialise()
    controller.check_filled()

    assert set(controller.attributes) == {"discovered"}


def test_a_trailing_underscore_is_dropped_from_the_name():
    # ophyd-async's convention for a name that would otherwise clash.
    class Declared(Controller):
        description_: AttrR[str]

    controller = Declared()

    assert "description" in controller.attributes
    assert controller.attributes["description"].dtype is str


def test_extras_from_an_annotated_hint_are_handed_back():
    class SCPIParam:
        def __init__(self, param: str) -> None:
            self.param = param

    spec = SCPIParam("P")

    class Declared(Controller):
        power: Annotated[AttrRW[float], spec]

    controller = Declared()

    assert controller.filler.declarations["power"].extras == (spec,)
    assert list(controller.filler) == [(controller.power, (spec,))]


def test_extras_survive_an_optional_annotated_hint():
    spec = object()

    class Declared(Controller):
        maybe: Annotated[AttrR[int], spec] | None

    declaration = Declared().filler.declarations["maybe"]

    assert declaration.extras == (spec,)
    assert declaration.optional


def test_a_sub_controller_hint_is_promised_not_created():
    class Child(Controller):
        pass

    class Parent(Controller):
        child: Child

    controller = Parent()

    assert controller.sub_controllers == {}

    with pytest.raises(RuntimeError, match="child .declared Child, never added."):
        controller.check_filled()

    controller.add_sub_controller("child", Child())
    controller.check_filled()


def test_a_controller_vector_hint_is_promised_not_created():
    class Child(Controller):
        pass

    class Parent(Controller):
        children: ControllerVector[Child]

    controller = Parent()

    with pytest.raises(RuntimeError, match="children .declared ControllerVector"):
        controller.check_filled()

    controller.add_sub_controller("children", ControllerVector({1: Child()}))
    controller.check_filled()


def test_check_filled_reports_every_missing_declaration_at_once():
    class Declared(Controller):
        one: AttrR
        two: AttrR

    with pytest.raises(RuntimeError, match="one .*, two .*"):
        Declared().check_filled()


def test_check_filled_recurses_into_sub_controllers():
    class Child(Controller):
        promised: AttrR

    class Parent(Controller):
        def __init__(self) -> None:
            super().__init__()
            self.child = Child()

    with pytest.raises(RuntimeError, match="promised .declared AttrR"):
        Parent().check_filled()


def test_check_filled_names_its_source():
    class Declared(Controller):
        promised: AttrR

    with pytest.raises(RuntimeError, match="did not provision from the parameter tree"):
        Declared().check_filled("the parameter tree")


def test_a_class_body_attribute_instance_is_rejected():
    class Shared(Controller):
        attr = AttrR(int)

    with pytest.raises(TypeError, match="Shared.attr is an AttrR in the class body"):
        Shared()


@pytest.mark.asyncio
async def test_a_decorated_attribute_satisfies_a_hint_of_the_same_name():
    from fastcs.attributes import attr

    class Declared(Controller):
        voltage: AttrR[float]  # pyright: ignore[reportRedeclaration]

        @attr
        async def voltage(self) -> float:
            return 1.5

    controller = Declared()

    # The decorator provided it, so the filler did not create a second one.
    assert await controller.voltage.poll() == 1.5
    controller.check_filled()


def test_a_decorated_attribute_disagreeing_with_its_hint_raises():
    from fastcs.attributes import attr

    class Declared(Controller):
        voltage: AttrR[int]  # pyright: ignore[reportRedeclaration]

        @attr
        async def voltage(self) -> float:
            return 1.5

    with pytest.raises(RuntimeError, match="does not match defined datatype"):
        Declared()


@pytest.mark.asyncio
async def test_filling_only_a_getter_leaves_a_read_only_attribute_readable():
    class Declared(Controller):
        reading: AttrR[float]

    controller = Declared()

    async def get() -> float:
        return 2.5

    controller.filler.fill_attribute("reading", getter=NotPolled(get))

    assert controller.reading.poll_period is None
    assert await controller.reading.poll() == 2.5


def test_filling_a_setter_on_a_read_only_attribute_raises():
    class Declared(Controller):
        reading: AttrR[float]

    controller = Declared()

    async def put(value: float) -> None:
        pass

    with pytest.raises(TypeError, match="nothing to write"):
        controller.filler.fill_attribute("reading", setter=put)


def test_filling_a_getter_on_a_write_only_attribute_raises():
    class Declared(Controller):
        demand: AttrW[float]

    controller = Declared()

    async def get() -> float:
        return 0.0

    with pytest.raises(TypeError, match="nothing to read"):
        controller.filler.fill_attribute("demand", getter=get)


def test_filling_twice_raises():
    class Declared(Controller):
        reading: AttrR[float]

    controller = Declared()

    async def get() -> float:
        return 0.0

    controller.filler.fill_attribute("reading", getter=get)

    with pytest.raises(ValueError, match="already has a getter"):
        controller.filler.fill_attribute("reading", getter=get)


def test_fill_meta_takes_a_whole_meta_dict():
    class Declared(Controller):
        reading: AttrR[float]

    controller = Declared()
    controller.filler.fill_meta("reading", {"units": "mm", "precision": 2})

    assert controller.reading.meta == {"units": "mm", "precision": 2}


def test_a_method_hint_is_promised():
    async def noop() -> None:
        pass

    class Declared(Controller):
        sweep: Scan

    controller = Declared()

    with pytest.raises(RuntimeError, match="sweep .declared Scan, never added."):
        controller.check_filled()

    with pytest.raises(RuntimeError, match="Cannot add command method"):
        controller.add_command("sweep", Command(noop))

    controller.add_scan("sweep", Scan(fn=noop, period=0.1))
    controller.check_filled()


def test_hinted_attributes_are_not_shared_between_instances():
    class Declared(Controller):
        count: AttrRW[int]

    one, two = Declared(), Declared()

    assert one.count is not two.count


def test_attributes_can_be_added_to_a_bare_controller_from_outside():
    # What a filler does, and what `fastcs-catio` moves to instead of building
    # Controller classes at runtime with `type(...)` - ADR 0013, question 2.
    controller = Controller()
    controller.add_attribute("discovered", AttrR(int))
    controller.temperature = AttrRW(float)

    assert set(controller.attributes) == {"discovered", "temperature"}
    controller.check_filled()
