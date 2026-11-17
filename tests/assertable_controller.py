import copy
from contextlib import contextmanager
from typing import Literal

from pytest_mock import MockerFixture, MockType

from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerAPI
from fastcs.methods import command, scan


class TestSubController(Controller):
    def __init__(self) -> None:
        super().__init__()
        self.read_int = AttrR(int)


class MyTestController(Controller):
    def __init__(self) -> None:
        super().__init__()

        self._sub_controllers: list[TestSubController] = []
        for index in range(1, 3):
            controller = TestSubController()
            self._sub_controllers.append(controller)
            self.add_sub_controller(f"SubController{index:02d}", controller)

    initialised = False
    count = 0

    async def initialise(self) -> None:
        await super().initialise()
        self.initialised = True

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @command()
    async def go(self):
        pass

    @scan(0.01)
    async def counter(self):
        self.count += 1


class AssertableControllerAPI(ControllerAPI):
    def __init__(
        self,
        controller: Controller,
        mocker: MockerFixture,
        path: list[str] | None = None,
    ) -> None:
        super().__init__()

        self.mocker = mocker
        self.command_method_spys: dict[str, MockType] = {}

        # Build a ControllerAPI from the given Controller
        controller_api = controller._build_api(path or [])
        # Copy its fields
        self.path = controller_api.path
        self.attributes = controller_api.attributes
        self.command_methods = controller_api.command_methods
        self.scan_methods = controller_api.scan_methods
        self.sub_apis = controller_api.sub_apis

        # Create spys for command methods before they are passed to the transport
        for command_name in self.command_methods.keys():
            self.command_method_spys[command_name] = mocker.spy(
                self.command_methods[command_name], "_fn"
            )

    @contextmanager
    def assert_read_here(self, path: list[str]):
        yield from self._assert_readback(path)

    @contextmanager
    def assert_write_here(self, path: list[str]):
        yield from self._assert_method(path, "set")

    @contextmanager
    def assert_execute_here(self, path: list[str]):
        yield from self._assert_method(path, "")

    def _navigate(self, path: list[str]) -> tuple[ControllerAPI, str]:
        queue = copy.deepcopy(path)
        controller_api: ControllerAPI = self
        item_name = queue.pop(-1)
        for item in queue:
            controller_api = controller_api.sub_apis[item]
        return controller_api, item_name

    def _assert_readback(self, path: list[str]):
        """Confirm that an attribute's ``readback`` property is read exactly once
        within a context block.

        ``readback`` is a read-only property, so it can't be spied on with
        ``mocker.spy`` (which needs to reassign the instance attribute). Instead,
        temporarily replace the property on the attribute's class with a counting
        wrapper, scoped to just this one instance.
        """
        controller_api, item_name = self._navigate(path)
        attr = controller_api.attributes[item_name]
        assert isinstance(attr, AttrR)
        cls = type(attr)
        original = cls.readback
        assert original.fget is not None
        original_fget = original.fget
        call_count = {"n": 0}

        def fget(self):
            if self is attr:
                call_count["n"] += 1
            return original_fget(self)

        cls.readback = property(fget)  # type: ignore[misc]
        try:
            yield  # Enter context
        except Exception as e:
            raise e
        else:  # Exit context
            assert call_count["n"] == 1, (
                f"Expected {'.'.join(path + ['readback'])} to be read once, "
                f"but it was read {call_count['n']} times."
            )
        finally:
            cls.readback = original  # type: ignore[misc]

    def _assert_method(self, path: list[str], method: Literal["set", ""]):
        """
        This context manager can be used to confirm that a fastcs
        controller's respective attribute or command methods are called
        a single time within a context block
        """
        controller_api, item_name = self._navigate(path)

        # Get spy
        if method:
            attr = controller_api.attributes[item_name]
            spy = self.mocker.spy(attr, method)
        else:
            # Lookup pre-defined spy for method
            spy = self.command_method_spys[item_name]

        initial = spy.call_count
        try:
            yield  # Enter context
        except Exception as e:
            raise e
        else:  # Exit context
            final = spy.call_count
            assert final == initial + 1, (
                f"Expected {'.'.join(path + [method] if method else path)} "
                f"to be called once, but it was called {final - initial} times."
            )
