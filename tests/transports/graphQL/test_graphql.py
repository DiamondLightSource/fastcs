import asyncio
import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from tests.assertable_controller import (
    AssertableControllerAPI,
    MyTestController,
)

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.transports.graphql.transport import GraphQLTransport


class GraphQLController(MyTestController):
    read_int: AttrR[int]
    read_write_int: AttrRW[int]
    read_write_float: AttrRW[float]
    read_bool: AttrR[bool]
    write_bool: AttrW[bool]
    read_string: AttrRW[str]


_GQL_ID = "device"


@pytest.fixture(scope="class")
def gql_controller_api(class_mocker: MockerFixture):
    return AssertableControllerAPI(GraphQLController(), class_mocker, path=[_GQL_ID])


def nest_query(path: list[str]) -> str:
    queue = copy.deepcopy(path)
    field = queue.pop(0)

    if queue:
        nesting = nest_query(queue)
        return f"{field} {{ {nesting} }} "
    else:
        return field


def nest_mutation(path: list[str], value: Any) -> str:
    queue = copy.deepcopy(path)
    field = queue.pop(0)

    if queue:
        nesting = nest_mutation(queue, value)
        return f"{field} {{ {nesting} }} "
    else:
        return f"{field}(value: {json.dumps(value)})"


def nest_response(path: list[str], value: Any) -> dict:
    queue = copy.deepcopy(path)
    field = queue.pop(0)

    if queue:
        nesting = nest_response(queue, value)
        return {field: nesting}
    else:
        return {field: value}


def create_test_client(gql_controller_api: AssertableControllerAPI) -> TestClient:
    graphql_transport = GraphQLTransport()
    graphql_transport.connect([gql_controller_api], asyncio.AbstractEventLoop())
    return TestClient(graphql_transport._server._app)


class TestGraphQLServer:
    @pytest.fixture(scope="class")
    def test_client(self, gql_controller_api) -> TestClient:
        return create_test_client(gql_controller_api)

    def test_read_int(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = [_GQL_ID, "readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_read_write_int(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = [_GQL_ID, "readWriteInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_write_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

        new = 9
        mutation = f"mutation {{ {nest_mutation(path, new)} }}"
        with gql_controller_api.assert_write_here(["read_write_int"]):
            response = test_client.post("/graphql", json={"query": mutation})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, new)

    def test_read_write_float(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = [_GQL_ID, "readWriteFloat"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_write_float"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

        new = 0.5
        mutation = f"mutation {{ {nest_mutation(path, new)} }}"
        with gql_controller_api.assert_write_here(["read_write_float"]):
            response = test_client.post("/graphql", json={"query": mutation})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, new)

    def test_read_bool(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = False
        path = [_GQL_ID, "readBool"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_bool"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_write_bool(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        value = True
        path = [_GQL_ID, "writeBool"]
        mutation = f"mutation {{ {nest_mutation(path, value)} }}"
        with gql_controller_api.assert_write_here(["write_bool"]):
            response = test_client.post("/graphql", json={"query": mutation})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, value)

    def test_go(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        test_client = create_test_client(gql_controller_api)

        path = [_GQL_ID, "go"]
        mutation = f"mutation {{ {nest_query(path)} }}"
        with gql_controller_api.assert_execute_here(["go"]):
            response = test_client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, True)

    def test_read_child1(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = [_GQL_ID, "SubController01", "readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["SubController01", "read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_read_child2(self, gql_controller_api, test_client: TestClient):
        expect = 0
        path = [_GQL_ID, "SubController02", "readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["SubController02", "read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)
