import asyncio
import enum
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import Float
from fastcs.methods import UnboundCommand, command
from fastcs.transports.epics.ca.ioc import EpicsCAIOC
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.pva.ioc import parse_attributes
from fastcs.transports.graphql.transport import GraphQLTransport
from fastcs.transports.rest.transport import RestTransport
from fastcs.transports.tango.dsr import _collect_dev_commands, _unservable_reason


class TypedCommandController(Controller):
    """A controller with one command of each shape."""

    calls: list[tuple] = []

    # The GraphQL transport refuses an API with nothing to read
    position = AttrR(Float())

    # Void
    @command()
    async def stop(self) -> None:
        self.calls.append(())

    # Arguments with no return
    @command()
    async def move_to(self, position: float, wait: bool) -> None:
        self.calls.append((position, wait))

    # Return with no arguments
    @command()
    async def measure(self) -> float:
        return 1.5

    # Arguments and return
    @command()
    async def scale(self, factor: float) -> float:
        return factor * 2


@pytest.fixture
def controller_api() -> ControllerAPI:
    TypedCommandController.calls = []
    return TypedCommandController()._build_api(["DEVICE"])


class TestRest:
    """REST carries arguments in the request body and a result in the response."""

    @pytest.fixture
    def client(self, controller_api) -> Iterator[TestClient]:
        transport = RestTransport()
        transport.connect([controller_api], asyncio.AbstractEventLoop())
        with TestClient(transport._server._app) as client:
            yield client

    def test_void_command_answers_no_content(self, client):
        assert client.put("/DEVICE/stop").status_code == 204

    def test_arguments_are_taken_from_the_request_body(self, client):
        response = client.put("/DEVICE/move-to", json={"position": 2.5, "wait": True})

        assert response.status_code == 204
        assert TypedCommandController.calls == [(2.5, True)]

    def test_a_missing_argument_is_rejected(self, client):
        response = client.put("/DEVICE/move-to", json={"position": 2.5})

        assert response.status_code == 422
        assert TypedCommandController.calls == []

    def test_return_value_comes_back_in_the_body(self, client):
        response = client.put("/DEVICE/measure")

        assert response.status_code == 200
        assert response.json() == {"value": 1.5}

    def test_arguments_and_a_return_value_together(self, client):
        response = client.put("/DEVICE/scale", json={"factor": 3.0})

        assert response.status_code == 200
        assert response.json() == {"value": 6.0}


class TestGraphQL:
    """GraphQL carries arguments as mutation arguments and a result as its value."""

    @pytest.fixture
    def client(self, controller_api) -> Iterator[TestClient]:
        transport = GraphQLTransport()
        transport.connect([controller_api], asyncio.AbstractEventLoop())
        # Strawberry's app rejects the lifespan startup that `with` sends
        client = TestClient(transport._server._app)
        yield client
        client.close()

    def query(self, client: TestClient, mutation: str):
        response = client.post("/graphql", json={"query": f"mutation {{ {mutation} }}"})
        assert response.status_code == 200
        body = response.json()
        assert "errors" not in body, body["errors"]
        return body["data"]

    def test_void_command_reports_that_it_ran(self, client):
        assert self.query(client, "DEVICE { stop }") == {"DEVICE": {"stop": True}}

    def test_arguments_are_mutation_arguments(self, client):
        assert self.query(client, "DEVICE { moveTo(position: 2.5, wait: true) }") == {
            "DEVICE": {"moveTo": True}
        }
        assert TypedCommandController.calls == [(2.5, True)]

    def test_a_missing_argument_is_rejected(self, client):
        response = client.post(
            "/graphql", json={"query": "mutation { DEVICE { moveTo(position: 2.5) } }"}
        )

        [error] = response.json()["errors"]
        assert "argument 'wait'" in error["message"]
        assert TypedCommandController.calls == []

    def test_return_value_is_the_mutation_result(self, client):
        assert self.query(client, "DEVICE { scale(factor: 3.0) }") == {
            "DEVICE": {"scale": 6.0}
        }


class TestEpicsCA:
    """EPICS CA serves only void commands and skips the rest."""

    def test_typed_commands_are_skipped_and_void_ones_are_not(self, controller_api):
        """A typed command must not stop the void ones being served."""
        EpicsCAIOC([controller_api], aliases={})

        assert {
            name: method.enabled
            for name, method in controller_api.command_methods.items()
        } == {
            "stop": True,
            "move_to": False,
            "measure": False,
            "scale": False,
        }

    def test_skipping_says_why(self, controller_api, loguru_caplog):
        EpicsCAIOC([controller_api], aliases={})

        assert (
            "EPICS CA transport cannot serve a command that takes arguments or "
            "returns a value" in loguru_caplog.text
        )


class TestEpicsPva:
    """EPICS PVA serves only void commands and skips the rest."""

    @pytest.mark.asyncio
    async def test_typed_commands_are_skipped_and_void_ones_are_not(
        self, controller_api
    ):
        provider = await parse_attributes(controller_api)

        assert "DEVICE:Stop" in provider.keys()
        assert "DEVICE:MoveTo" not in provider.keys()
        assert {
            name: method.enabled
            for name, method in controller_api.command_methods.items()
        } == {
            "stop": True,
            "move_to": False,
            "measure": False,
            "scale": False,
        }

    @pytest.mark.asyncio
    async def test_skipping_says_why(self, controller_api, loguru_caplog):
        await parse_attributes(controller_api)

        assert (
            "EPICS PVA transport cannot serve a command that takes arguments or "
            "returns a value" in loguru_caplog.text
        )


class TestEpicsGui:
    """The EPICS GUI shows only what the IOC serves."""

    def test_a_command_the_ioc_skipped_gets_no_widget(self, controller_api):
        EpicsCAIOC([controller_api], aliases={})

        components = EpicsGUI(controller_api).extract_api_components(controller_api)

        # move_to, measure, and scale are skipped
        assert [component.name for component in components] == ["Position", "Stop"]

    def test_a_disabled_attribute_gets_no_widget(self, controller_api):
        controller_api.attributes["position"].enabled = False

        components = EpicsGUI(controller_api).extract_api_components(controller_api)

        assert "Position" not in [component.name for component in components]


class TestTango:
    """Tango carries one argument at most, and no enum, so it declares that."""

    def test_serves_a_void_command(self):
        bound_command = UnboundCommand(TypedCommandController.stop).bind(
            TypedCommandController()
        )

        assert _unservable_reason(bound_command) is None

    def test_serves_one_argument_and_a_return_value(self):
        bound_command = UnboundCommand(TypedCommandController.scale).bind(
            TypedCommandController()
        )

        assert _unservable_reason(bound_command) is None

    def test_refuses_more_than_one_argument(self):
        bound_command = UnboundCommand(TypedCommandController.move_to).bind(
            TypedCommandController()
        )

        assert (
            _unservable_reason(bound_command)
            == "a Tango command takes at most one argument"
        )

    def test_refuses_a_datatype_it_cannot_carry(self):
        class Colour(enum.Enum):
            RED = "red"

        class EnumCommandController(Controller):
            @command()
            async def set_colour(self, colour: Colour) -> None:
                pass

        # api = EnumCommandController()._build_api(["DEVICE"])
        bound_command = UnboundCommand(EnumCommandController.set_colour).bind(
            EnumCommandController()
        )

        assert _unservable_reason(bound_command) == "Tango commands do not carry Colour"

    def test_only_servable_commands_are_collected(self, controller_api, mocker):
        collection = _collect_dev_commands(controller_api, mocker.MagicMock())

        assert sorted(collection) == ["Measure", "Scale", "Stop"]
        assert not controller_api.command_methods["move_to"].enabled
