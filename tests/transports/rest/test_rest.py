import enum

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from tests.assertable_controller import AssertableControllerAPI, MyTestController

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import Bool, Enum, Float, Int, String, Waveform
from fastcs.transports.rest.transport import RestTransport


class RestController(MyTestController):
    read_int = AttrR(Int())
    read_write_int = AttrRW(Int())
    read_write_float = AttrRW(Float())
    read_bool = AttrR(Bool())
    write_bool = AttrW(Bool())
    read_string = AttrRW(String())
    enum = AttrRW(Enum(enum.IntEnum("Enum", {"RED": 0, "GREEN": 1, "BLUE": 2})))
    one_d_waveform = AttrRW(Waveform(np.int32, (10,)))
    two_d_waveform = AttrRW(Waveform(np.int32, (10, 10)))


@pytest.fixture(scope="class")
def rest_controller_api(class_mocker: MockerFixture):
    return AssertableControllerAPI(RestController(), class_mocker)


def create_test_client(rest_controller_api: ControllerAPI) -> TestClient:
    rest_transport = RestTransport()
    rest_transport.connect(rest_controller_api)
    return TestClient(rest_transport._server._app)


class TestRestServer:
    @pytest.fixture(scope="class")
    def test_client(self, rest_controller_api):
        with create_test_client(rest_controller_api) as test_client:
            yield test_client

    def test_read_write_int(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        with rest_controller_api.assert_read_here(["read_write_int"]):
            response = test_client.get("/read-write-int")
        assert response.status_code == 200
        assert response.json()["value"] == expect
        new = 9
        with rest_controller_api.assert_write_here(["read_write_int"]):
            response = test_client.put("/read-write-int", json={"value": new})
        assert test_client.get("/read-write-int").json()["value"] == new

    def test_read_int(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        with rest_controller_api.assert_read_here(["read_int"]):
            response = test_client.get("/read-int")
        assert response.status_code == 200
        assert response.json()["value"] == expect

    def test_read_write_float(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        with rest_controller_api.assert_read_here(["read_write_float"]):
            response = test_client.get("/read-write-float")
        assert response.status_code == 200
        assert response.json()["value"] == expect
        new = 0.5
        with rest_controller_api.assert_write_here(["read_write_float"]):
            response = test_client.put("/read-write-float", json={"value": new})
        assert test_client.get("/read-write-float").json()["value"] == new

    def test_read_bool(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = False
        with rest_controller_api.assert_read_here(["read_bool"]):
            response = test_client.get("/read-bool")
        assert response.status_code == 200
        assert response.json()["value"] == expect

    def test_write_bool(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        with rest_controller_api.assert_write_here(["write_bool"]):
            test_client.put("/write-bool", json={"value": True})

    def test_enum(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        enum_attr = rest_controller_api.attributes["enum"]
        assert isinstance(enum_attr, AttrRW)
        enum_cls = enum_attr.datatype.dtype
        assert isinstance(enum_attr.get(), enum_cls)
        assert enum_attr.get() == enum_cls(0)
        expect = 0
        with rest_controller_api.assert_read_here(["enum"]):
            response = test_client.get("/enum")
        assert response.status_code == 200
        assert response.json()["value"] == expect
        new = 2
        with rest_controller_api.assert_write_here(["enum"]):
            response = test_client.put("/enum", json={"value": new})
        assert test_client.get("/enum").json()["value"] == new
        assert isinstance(enum_attr.get(), enum_cls)
        assert enum_attr.get() == enum_cls(2)

    def test_1d_waveform(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        attribute = rest_controller_api.attributes["one_d_waveform"]
        expect = np.zeros((10,), dtype=np.int32)
        assert isinstance(attribute, AttrRW)
        assert np.array_equal(attribute.get(), expect)
        assert isinstance(attribute.get(), np.ndarray)

        with rest_controller_api.assert_read_here(["one_d_waveform"]):
            response = test_client.get("one-d-waveform")
        assert np.array_equal(response.json()["value"], expect)
        new = [1, 2, 3]
        with rest_controller_api.assert_write_here(["one_d_waveform"]):
            test_client.put("/one-d-waveform", json={"value": new})
        assert np.array_equal(test_client.get("/one-d-waveform").json()["value"], new)

        result = test_client.get("/one-d-waveform")
        assert np.array_equal(result.json()["value"], new)
        assert np.array_equal(attribute.get(), new)
        assert isinstance(attribute.get(), np.ndarray)

    def test_2d_waveform(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        attribute = rest_controller_api.attributes["two_d_waveform"]
        assert isinstance(attribute, AttrRW)
        expect = np.zeros((10, 10), dtype=np.int32)
        assert np.array_equal(attribute.get(), expect)
        assert isinstance(attribute.get(), np.ndarray)

        with rest_controller_api.assert_read_here(["two_d_waveform"]):
            result = test_client.get("/two-d-waveform")
        assert np.array_equal(result.json()["value"], expect)
        new = [[1, 2, 3], [4, 5, 6]]
        with rest_controller_api.assert_write_here(["two_d_waveform"]):
            test_client.put("/two-d-waveform", json={"value": new})

        result = test_client.get("/two-d-waveform")
        assert np.array_equal(result.json()["value"], new)
        assert np.array_equal(attribute.get(), new)
        assert isinstance(attribute.get(), np.ndarray)

    def test_go(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        with rest_controller_api.assert_execute_here(["go"]):
            response = test_client.put("/go")

        assert response.status_code == 204

    def test_read_child1(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        with rest_controller_api.assert_read_here(["SubController01", "read_int"]):
            response = test_client.get("/SubController01/read-int")
        assert response.status_code == 200
        assert response.json()["value"] == expect

    def test_read_child2(
        self, rest_controller_api: AssertableControllerAPI, test_client: TestClient
    ):
        expect = 0
        with rest_controller_api.assert_read_here(["SubController02", "read_int"]):
            response = test_client.get("/SubController02/read-int")
        assert response.status_code == 200
        assert response.json()["value"] == expect
