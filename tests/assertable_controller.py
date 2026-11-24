import copy
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from pytest_mock import MockerFixture, MockType

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import DType_T, Int
from fastcs.methods import command, scan


@dataclass
class MyTestAttributeIORef(AttributeIORef):
    update_period = 1


class MyTestAttributeIO(AttributeIO[DType_T, MyTestAttributeIORef]):
    async def update(self, attr: AttrR[DType_T, MyTestAttributeIORef]):
        print(f"update {attr}")

    async def send(self, attr: AttrW[DType_T, MyTestAttributeIORef], value: DType_T):
        print(f"sending {attr} = {value}")
        if isinstance(attr, AttrRW):
            await attr.update(value)


test_attribute_io = MyTestAttributeIO()  # instance


class TestSubController(Controller):
    read_int: AttrR = AttrR(Int(), io_ref=MyTestAttributeIORef())

    def __init__(self) -> None:
        super().__init__(ios=[test_attribute_io])


class MyTestController(Controller):
    def __init__(self) -> None:
        super().__init__(ios=[test_attribute_io])

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
        yield from self._assert_method(path, "get")

    @contextmanager
    def assert_write_here(self, path: list[str]):
        yield from self._assert_method(path, "put")

    @contextmanager
    def assert_execute_here(self, path: list[str]):
        yield from self._assert_method(path, "")

    def _assert_method(self, path: list[str], method: Literal["get", "put", ""]):
        """
        This context manager can be used to confirm that a fastcs
        controller's respective attribute or command methods are called
        a single time within a context block
        """
        queue = copy.deepcopy(path)

        # Navigate to sub controller
        controller_api = self
        item_name = queue.pop(-1)
        for item in queue:
            controller_api = controller_api.sub_apis[item]

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
