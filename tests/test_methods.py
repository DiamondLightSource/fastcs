from inspect import signature

import pytest

from fastcs.controllers import Controller
from fastcs.methods import Command, Scan
from fastcs.methods.command import UnboundCommand
from fastcs.methods.method import Method
from fastcs.methods.scan import UnboundScan


def test_method():
    def sync_do_nothing():
        pass

    with pytest.raises(TypeError):
        Method(sync_do_nothing)  # type: ignore

    async def do_nothing():
        """Do nothing."""
        pass

    method = Method(do_nothing, group="Nothing")

    assert method.docstring == "Do nothing."
    assert method.group == "Nothing"
    assert method.signature == signature(do_nothing)


def test_a_scan_takes_no_arguments_and_returns_nothing():
    async def scan_with_return() -> int:
        return 1

    with pytest.raises(TypeError, match="Scan method return type must be None"):
        Scan(scan_with_return, 1.0)  # type: ignore

    async def scan_with_argument(arg: int):
        pass

    with pytest.raises(TypeError, match="Scan method cannot have arguments"):
        Scan(scan_with_argument, 1.0)  # type: ignore


@pytest.mark.asyncio
async def test_unbound_command():
    class TestController(Controller):
        async def do_nothing(self):
            pass

        async def do_nothing_with_arg(self, arg):
            pass

    with pytest.raises(TypeError):
        UnboundCommand(TestController.do_nothing_with_arg)  # type: ignore

    with pytest.raises(TypeError):
        Command(TestController().do_nothing_with_arg)  # type: ignore

    unbound_command = UnboundCommand(TestController.do_nothing, group="Test")
    command = unbound_command.bind(TestController())
    # Test that group is passed when binding commands
    assert command.group == "Test"

    await command()


@pytest.mark.asyncio
async def test_unbound_scan():
    class TestController(Controller):
        async def update_nothing(self):
            pass

        async def update_nothing_with_arg(self, arg):
            pass

    with pytest.raises(TypeError):
        UnboundScan(TestController.update_nothing_with_arg, 1.0)  # type: ignore

    with pytest.raises(TypeError):
        Scan(TestController().update_nothing_with_arg, 1.0)  # type: ignore

    unbound_scan = UnboundScan(TestController.update_nothing, 1.0)
    assert unbound_scan.period == 1.0
    scan = unbound_scan.bind(TestController())

    assert scan.period == 1.0

    await scan()


@pytest.mark.asyncio
async def test_a_command_can_take_arguments_and_return_a_value():
    class TestController(Controller):
        async def move_to(self, position: float, wait: bool) -> str:
            return f"moved to {position}, waited {wait}"

    command = UnboundCommand(TestController.move_to).bind(TestController())

    assert command.argument_types == (float, bool)
    assert command.return_datatype is str
    assert not command.is_void
    assert await command(1.5, True) == "moved to 1.5, waited True"


@pytest.mark.asyncio
async def test_a_void_command_says_so():
    class TestController(Controller):
        async def stop(self):
            pass

    command = UnboundCommand(TestController.stop).bind(TestController())

    assert command.argument_types == ()
    assert command.return_datatype is None
    assert command.is_void


def test_command_arguments_must_be_annotated():
    class TestController(Controller):
        async def move_to(self, position):
            pass

    with pytest.raises(TypeError, match="Argument 'position'.*has no type annotation"):
        UnboundCommand(TestController.move_to)


def test_command_arguments_must_be_a_supported_type():
    class TestController(Controller):
        async def move_to(self, position: list[float]):
            pass

    with pytest.raises(TypeError, match="Argument 'position'.*unsupported type"):
        UnboundCommand(TestController.move_to)


def test_command_return_must_be_a_supported_type():
    class TestController(Controller):
        async def measure(self) -> list[float]:
            return []

    with pytest.raises(TypeError, match="Return value.*unsupported type"):
        UnboundCommand(TestController.measure)


def test_command_arguments_are_positional():
    class TestController(Controller):
        async def move_to(self, *, position: float):
            pass

    with pytest.raises(TypeError, match="keyword-only argument 'position'"):
        UnboundCommand(TestController.move_to)


def test_command_arguments_must_be_fully_known():
    class TestController(Controller):
        async def move_to(self, *args: float):
            pass

    with pytest.raises(TypeError, match=r"takes \*args or \*\*kwargs"):
        UnboundCommand(TestController.move_to)


@pytest.mark.asyncio
async def test_command_arguments_survive_binding():
    """The signature a transport reads must be the bound one, without ``self``."""

    class TestController(Controller):
        seen: list[float] = []

        async def move_to(self, position: float) -> None:
            self.seen.append(position)

    controller = TestController()
    command = UnboundCommand(TestController.move_to).bind(controller)

    assert list(command.signature.parameters) == ["position"]
    await command(2.5)
    assert controller.seen == [2.5]
