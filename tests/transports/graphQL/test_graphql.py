import copy
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from tests.assertable_controller import (
    AssertableControllerAPI,
    MyTestAttributeIORef,
    MyTestController,
)

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.datatypes import Bool, Float, Int, String
from fastcs.transports.graphql.transport import GraphQLTransport


class GraphQLController(MyTestController):
    read_int = AttrR(Int(), io_ref=MyTestAttributeIORef())
    read_write_int = AttrRW(Int(), io_ref=MyTestAttributeIORef())
    read_write_float = AttrRW(Float())
    read_bool = AttrR(Bool())
    write_bool = AttrW(Bool(), io_ref=MyTestAttributeIORef())
    read_string = AttrRW(String())


@pytest.fixture(scope="class")
def gql_controller_api(class_mocker: MockerFixture):
    return AssertableControllerAPI(GraphQLController(), class_mocker)


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
        nesting = nest_query(queue)
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
    graphql_transport.connect(gql_controller_api)
    return TestClient(graphql_transport._server._app)


class TestGraphQLServer:
    @pytest.fixture(scope="class")
    def test_client(self, gql_controller_api) -> TestClient:
        return create_test_client(gql_controller_api)

    def test_read_int(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = ["readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_read_write_int(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = ["readWriteInt"]
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
        path = ["readWriteFloat"]
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
        path = ["readBool"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["read_bool"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_write_bool(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        value = True
        path = ["writeBool"]
        mutation = f"mutation {{ {nest_mutation(path, value)} }}"
        with gql_controller_api.assert_write_here(["write_bool"]):
            response = test_client.post("/graphql", json={"query": mutation})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, value)

    def test_go(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        test_client = create_test_client(gql_controller_api)

        path = ["go"]
        mutation = f"mutation {{ {nest_query(path)} }}"
        with gql_controller_api.assert_execute_here(["go"]):
            response = test_client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        assert response.json()["data"] == {path[-1]: True}

    def test_read_child1(
        self, gql_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        path = ["SubController01", "readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["SubController01", "read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)

    def test_read_child2(self, gql_controller_api, test_client: TestClient):
        expect = 0
        path = ["SubController02", "readInt"]
        query = f"query {{ {nest_query(path)} }}"
        with gql_controller_api.assert_read_here(["SubController02", "read_int"]):
            response = test_client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        assert response.json()["data"] == nest_response(path, expect)
