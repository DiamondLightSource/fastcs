import asyncio
import enum

import pytest

from fastcs.attributes import AttrR, AttrRW, AttrW, Polled
from fastcs.controllers import Controller, ControllerVector
from fastcs.methods import Command, Scan, command, scan


def test_controller_nesting():
    controller = Controller()
    sub_controller = Controller()
    sub_sub_controller = Controller()

    controller.a = sub_controller
    sub_controller.b = sub_sub_controller

    assert sub_controller.path == ["a"]
    assert sub_sub_controller.path == ["a", "b"]
    assert controller.sub_controllers == {"a": sub_controller}
    assert sub_controller.sub_controllers == {"b": sub_sub_controller}

    with pytest.raises(ValueError, match=r"Cannot add sub controller"):
        controller.a = Controller()

    with pytest.raises(ValueError, match=r"already registered"):
        controller.c = sub_controller


class SomeSubController(Controller):
    def __init__(self):
        super().__init__()

    sub_attribute: AttrR[int]

    root_attribute = AttrR(int)


class SomeController(Controller):
    annotated_attr_not_defined_in_init: AttrR[int]
    equal_attr: AttrR[int]
    annotated_and_equal_attr: AttrR[int]

    def __init__(self, sub_controller: Controller):
        super().__init__()

        self.attr_on_object = AttrR(int)

        self.attributes["_attributes_attr"] = AttrR(int)
        self.attributes["_attributes_attr_equal"] = self.equal_attr

        self.sub_controller = sub_controller


def test_attribute_parsing():
    sub_controller = SomeSubController()
    controller = SomeController(sub_controller)

    assert set(controller.attributes.keys()) == {
        "_attributes_attr",
        "attr_on_object",
        "_attributes_attr_equal",
        "annotated_attr_not_defined_in_init",
        "annotated_and_equal_attr",
        "equal_attr",
        "sub_controller",
    }

    # Every hinted attribute is created for this instance alone, so two
    # controllers of the same class never share one.
    other = SomeController(SomeSubController())
    assert other.equal_attr is not controller.equal_attr
    assert other.annotated_and_equal_attr is not controller.annotated_and_equal_attr

    assert sub_controller.attributes == {
        "sub_attribute": sub_controller.sub_attribute,
    }


async def noop() -> None:
    pass


@pytest.mark.parametrize(
    "member_name, member_value, expected_error",
    [
        ("attr", AttrR(float), r"Cannot add attribute"),
        ("attr", Controller(), r"Cannot add sub controller"),
        ("attr", Command(noop), r"Cannot add command"),
        ("sub_controller", AttrR(int), r"Cannot add attribute"),
        ("sub_controller", Controller(), r"Cannot add sub controller"),
        ("sub_controller", Command(noop), r"Cannot add command"),
        ("cmd", AttrR(int), r"Cannot add attribute"),
        ("cmd", Controller(), r"Cannot add sub controller"),
        ("cmd", Command(noop), r"Cannot add command"),
    ],
)
def test_conflicting_attributes_and_controllers_and_commands(
    member_name, member_value, expected_error
):
    class ConflictingController(Controller):
        cmd = Command(noop)

        def __init__(self):
            super().__init__()
            self.attr = AttrR(int)
            self.sub_controller = Controller()

    controller = ConflictingController()

    with pytest.raises(ValueError, match=expected_error):
        setattr(controller, member_name, member_value)


def test_controller_raises_error_if_passed_numeric_sub_controller_name():
    sub_controller = SomeSubController()
    controller = SomeController(sub_controller)

    with pytest.raises(ValueError, match="Numeric-only names are not allowed"):
        controller.add_sub_controller("30", sub_controller)


def test_controller_vector_raises_error_if_add_sub_controller_called():
    controller_vector = ControllerVector({i: SomeSubController() for i in range(2)})

    with pytest.raises(NotImplementedError, match="Use __setitem__ instead"):
        controller_vector.add_sub_controller("subcontroller", SomeSubController())


def test_controller_vector_indexing():
    controller = SomeSubController()
    another_controller = SomeSubController()
    controller_vector = ControllerVector({1: another_controller})
    controller_vector[10] = controller
    assert controller_vector.sub_controllers["10"] == controller
    assert controller_vector[1] == another_controller
    assert len(controller_vector) == 2

    with pytest.raises(KeyError):
        _ = controller_vector[2]


def test_controller_vector_delitem_raises_exception():
    controller = SomeSubController()
    controller_vector = ControllerVector({1: controller})
    with pytest.raises(NotImplementedError, match="Cannot delete"):
        del controller_vector[1]


def test_controller_vector_iter():
    sub_controllers = {1: SomeSubController(), 2: SomeSubController()}
    controller_vector = ControllerVector(sub_controllers)

    for index, child in controller_vector.items():
        assert sub_controllers[index] == child


def test_a_hint_with_a_datatype_is_created_unfilled():
    class HintedController(Controller):
        read_write_int: AttrRW[int]

    controller = HintedController()

    # The rule from ADR 0013: it exists as soon as __init__ returns, so the
    # rest of __init__ may reference it.
    assert isinstance(controller.read_write_int, AttrRW)
    assert controller.read_write_int.dtype is int
    assert not controller.read_write_int.has_getter()
    assert not controller.read_write_int.has_setter()

    controller.check_filled()


@pytest.mark.asyncio
async def test_filling_a_hinted_attribute():
    class HintedController(Controller):
        read_write_int: AttrRW[int]

    controller = HintedController()
    attribute = controller.read_write_int

    async def get() -> int:
        return 7

    async def put(value: int) -> None:
        pass

    controller.filler.fill_attribute(
        "read_write_int", getter=Polled(get, period=0.5), setter=put, units="counts"
    )

    # Filled in place, so a reference taken during __init__ is still the one
    # that ends up serving the device.
    assert controller.read_write_int is attribute
    assert attribute.poll_period == 0.5
    assert attribute.meta.get("units") == "counts"
    assert await attribute.poll() == 7


def test_filling_the_wrong_datatype_raises():
    class HintedController(Controller):
        read_write_int: AttrRW[int]

    controller = HintedController()

    with pytest.raises(TypeError, match="wrong datatype"):
        controller.filler.fill_attribute("read_write_int", datatype=float)


def test_filling_metadata_the_datatype_has_no_use_for_raises():
    class HintedController(Controller):
        label: AttrR[str]

    controller = HintedController()

    with pytest.raises(TypeError, match="'precision' is not valid metadata for str"):
        controller.filler.fill_attribute("label", precision=3)


def test_filling_something_that_was_never_declared_raises():
    class HintedController(Controller):
        read_write_int: AttrRW[int]

    controller = HintedController()

    with pytest.raises(KeyError, match="no attribute declaration"):
        controller.filler.fill_attribute("not_declared")


class PromisedAttrController(Controller):
    # The datatype is only knowable over the wire, so the filler cannot build
    # this one - introspection must add it.
    state: AttrR


def test_a_hint_without_a_datatype_is_not_created():
    controller = PromisedAttrController()

    assert "state" not in controller.attributes


def test_a_hint_without_a_datatype_is_promised():
    controller = PromisedAttrController()

    with pytest.raises(RuntimeError, match="state .declared AttrR, never added."):
        controller.check_filled()


def test_adding_a_promised_attribute_with_the_wrong_access_mode_raises():
    controller = PromisedAttrController()

    with pytest.raises(RuntimeError, match="expected 'AttrR', got 'AttrW'"):
        controller.add_attribute("state", AttrW(int))


def test_adding_a_promised_attribute_satisfies_the_declaration():
    controller = PromisedAttrController()

    controller.add_attribute("state", AttrR(int))

    controller.check_filled()


def test_an_optional_hint_is_not_required():
    class HintedController(Controller):
        maybe: AttrR | None

    HintedController().check_filled()


class GoodEnum(enum.IntEnum):
    VAL = 0


class BadEnum(enum.IntEnum):
    VAL = 0


class EnumHintedController(Controller):
    colour: AttrRW[GoodEnum]


def test_filling_an_enum_attribute_with_another_enum_raises():
    controller = EnumHintedController()

    with pytest.raises(TypeError, match="wrong datatype"):
        controller.filler.fill_attribute("colour", datatype=BadEnum)


def test_filling_an_enum_attribute_with_the_declared_enum_is_accepted():
    controller = EnumHintedController()

    controller.filler.fill_attribute("colour", datatype=GoodEnum)

    assert controller.colour.dtype is GoodEnum


class SubControllerHintedController(Controller):
    child: SomeSubController


def test_a_sub_controller_hint_is_promised():
    controller = SubControllerHintedController()

    with pytest.raises(RuntimeError, match="child .declared SomeSubController"):
        controller.check_filled()


def test_adding_a_sub_controller_of_the_wrong_type_raises():
    controller = SubControllerHintedController()

    with pytest.raises(RuntimeError, match="expected 'SomeSubController'"):
        controller.add_sub_controller("child", Controller())


def test_adding_the_declared_sub_controller_satisfies_the_declaration():
    controller = SubControllerHintedController()

    controller.add_sub_controller("child", SomeSubController())

    controller.check_filled()


class MethodHintedController(Controller):
    method: Scan


def test_a_method_hint_is_promised():
    controller = MethodHintedController()

    with pytest.raises(RuntimeError, match="method .declared Scan, never added."):
        controller.check_filled()


def test_adding_a_method_of_the_wrong_kind_raises():
    controller = MethodHintedController()

    with pytest.raises(RuntimeError, match="expected 'Scan', got 'Command'"):
        controller.add_command("method", Command(noop))


def test_adding_the_declared_method_satisfies_the_declaration():
    controller = MethodHintedController()

    controller.add_scan("method", Scan(fn=noop, period=0.1))

    controller.check_filled()


def test_controller_api():
    class MyTestController(Controller):
        def __init__(self):
            super().__init__(description="Controller for testing")
            self.attr1 = AttrRW(int)

            self.attr2 = AttrRW(int)

        @command()
        async def do_nothing(self):
            pass

        @scan(1.0)
        async def scan_nothing(self):
            pass

    controller = MyTestController()
    api = controller._build_api([])

    assert api.description == controller.description
    assert list(api.attributes) == ["attr1", "attr2"]
    assert list(api.command_methods) == ["do_nothing"]
    assert list(api.scan_methods) == ["scan_nothing"]


@pytest.mark.asyncio
async def test_scan_exception_sets_disconnected_and_reconnect_resumes():
    class MyTestController(Controller):
        @scan(0.01)
        async def failing_scan(self):
            raise RuntimeError("scan error")

    controller = MyTestController()
    controller.post_initialise()
    _, scan_coros, _ = controller.create_api_and_tasks()

    controller._connected = True
    task = asyncio.create_task(scan_coros[0]())

    # Wait long enough for the scan to run and raise, setting _connected = False
    await asyncio.sleep(0.1)
    assert not controller._connected

    # Trigger reconnect - _connected resumes scan tasks
    await controller.reconnect()
    assert controller._connected

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
