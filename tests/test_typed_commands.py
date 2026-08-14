"""Serving commands that take arguments and return values, per transport.

Each transport declares what it can carry: REST and GraphQL round-trip a typed
call, Tango carries at most one argument, and the EPICS transports are void-only
and skip anything else with a warning rather than refusing to serve the
controller (ADR 0015).
"""

import asyncio
import enum

import pytest
from fastapi.testclient import TestClient

from fastcs.attributes import AttrR
from fastcs.controllers import Controller, ControllerAPI
from fastcs.datatypes import Float
from fastcs.methods import command
from fastcs.transports.epics.ca.ioc import EpicsCAIOC
from fastcs.transports.graphql.transport import GraphQLTransport
from fastcs.transports.rest.transport import RestTransport
from fastcs.transports.tango.dsr import _unservable_reason


class TypedCommandController(Controller):
    """A controller with one command of each shape."""

    calls: list[tuple] = []

    # The GraphQL transport refuses an API with nothing to read
    position = AttrR(Float())

    @command()
    async def stop(self) -> None:
        self.calls.append(())

    @command()
    async def move_to(self, position: float, wait: bool) -> None:
        self.calls.append((position, wait))

    @command()
    async def measure(self) -> float:
        return 1.5

    @command()
    async def scale(self, factor: float) -> float:
        return factor * 2


@pytest.fixture
def controller_api() -> ControllerAPI:
    TypedCommandController.calls = []
    return TypedCommandController()._build_api(["DEVICE"])


def rest_client(controller_api: ControllerAPI) -> TestClient:
    transport = RestTransport()
    transport.connect([controller_api], asyncio.AbstractEventLoop())
    return TestClient(transport._server._app)


class TestRest:
    def test_void_command_answers_no_content(self, controller_api):
        with rest_client(controller_api) as client:
            assert client.put("/DEVICE/stop").status_code == 204

    def test_arguments_are_taken_from_the_request_body(self, controller_api):
        with rest_client(controller_api) as client:
            response = client.put(
                "/DEVICE/move-to", json={"position": 2.5, "wait": True}
            )

        assert response.status_code == 204
        assert TypedCommandController.calls == [(2.5, True)]

    def test_a_missing_argument_is_rejected(self, controller_api):
        with rest_client(controller_api) as client:
            response = client.put("/DEVICE/move-to", json={"position": 2.5})

        assert response.status_code == 422
        assert TypedCommandController.calls == []

    def test_return_value_comes_back_in_the_body(self, controller_api):
        with rest_client(controller_api) as client:
            response = client.put("/DEVICE/measure")

        assert response.status_code == 200
        assert response.json() == {"value": 1.5}

    def test_arguments_and_a_return_value_together(self, controller_api):
        with rest_client(controller_api) as client:
            response = client.put("/DEVICE/scale", json={"factor": 3.0})

        assert response.status_code == 200
        assert response.json() == {"value": 6.0}


class TestGraphQL:
    @pytest.fixture
    def client(self, controller_api) -> TestClient:
        transport = GraphQLTransport()
        transport.connect([controller_api], asyncio.AbstractEventLoop())
        return TestClient(transport._server._app)

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

    def test_return_value_is_the_mutation_result(self, client):
        assert self.query(client, "DEVICE { scale(factor: 3.0) }") == {
            "DEVICE": {"scale": 6.0}
        }


class TestEpicsCA:
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


class TestTango:
    """Tango carries one argument at most, and no enum, so it declares that."""

    def test_serves_a_void_command(self, controller_api):
        assert _unservable_reason(controller_api.command_methods["stop"]) is None

    def test_serves_one_argument_and_a_return_value(self, controller_api):
        assert _unservable_reason(controller_api.command_methods["scale"]) is None

    def test_refuses_more_than_one_argument(self, controller_api):
        assert (
            _unservable_reason(controller_api.command_methods["move_to"])
            == "a Tango command takes at most one argument"
        )

    def test_refuses_a_datatype_it_cannot_carry(self):
        class Colour(enum.Enum):
            RED = "red"

        class EnumCommandController(Controller):
            @command()
            async def set_colour(self, colour: Colour) -> None:
                pass

        api = EnumCommandController()._build_api(["DEVICE"])

        assert (
            _unservable_reason(api.command_methods["set_colour"])
            == "Tango commands do not carry Colour"
        )
