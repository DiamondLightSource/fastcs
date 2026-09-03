"""Tests for the multi-controller foundation slice (#353) and per-transport
multi-root extensions (#354+).
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from fastcs.attributes import AttrR
from fastcs.control_system import FastCS
from fastcs.controllers import Controller
from fastcs.transports.epics import EpicsDocsOptions, EpicsGUIOptions
from fastcs.transports.epics.ca.transport import EpicsCATransport
from fastcs.transports.epics.emission import INDEX_STEM
from fastcs.transports.epics.pva.transport import EpicsPVATransport
from fastcs.transports.graphql.transport import GraphQLTransport
from fastcs.transports.rest.transport import RestTransport


class _IdController(Controller):
    pass


class _OneAttrController(Controller):
    foo = AttrR(int)


class _OtherAttrController(Controller):
    bar = AttrR(int)


def test_controller_api_path_uses_id():
    controller = _IdController()
    sub = _IdController()
    controller.add_sub_controller("Sub", sub)
    controller.set_path(["X"])

    api, _, _ = controller.create_api_and_tasks()

    assert api.path == ["X"]
    assert api.sub_apis["Sub"].path == ["X", "Sub"]


def _api_with_id(controller_class: type[Controller], id: str):
    controller = controller_class()
    controller.set_path([id])
    api, _, _ = controller.create_api_and_tasks()
    return api


def test_rest_transport_routes_two_controllers_by_id():
    api1 = _api_with_id(_OneAttrController, "alpha")
    api2 = _api_with_id(_OtherAttrController, "beta")

    loop = asyncio.new_event_loop()
    try:
        transport = RestTransport()
        transport.connect([api1, api2], loop)

        with TestClient(transport._server._app) as client:
            assert client.get("/alpha/foo").status_code == 200
            assert client.get("/beta/bar").status_code == 200
    finally:
        loop.close()


def test_rest_transport_rejects_illegal_id_at_connect():
    api = _api_with_id(_OneAttrController, "bad/id")

    loop = asyncio.new_event_loop()
    try:
        transport = RestTransport()
        with pytest.raises(ValueError, match="bad/id"):
            transport.connect([api], loop)
    finally:
        loop.close()


def test_ca_transport_routes_two_controllers_with_distinct_pv_prefixes(mocker):
    """One softioc serves N controllers; each id is its verbatim PV prefix."""
    util_builder = mocker.patch("fastcs.transports.epics.ca.util.builder")
    mocker.patch("fastcs.transports.epics.ca.ioc.builder")

    api1 = _api_with_id(_OneAttrController, "ALPHA")
    api2 = _api_with_id(_OtherAttrController, "BETA")

    loop = asyncio.new_event_loop()
    try:
        transport = EpicsCATransport()
        transport.connect([api1, api2], loop)
    finally:
        loop.close()

    # Each controller's record lands under its verbatim id, no clash.
    util_builder.longIn.assert_any_call(
        "ALPHA:Foo",
        DESC=mocker.ANY,
        EGU=mocker.ANY,
        LOPR=mocker.ANY,
        HOPR=mocker.ANY,
        initial_value=mocker.ANY,
    )
    util_builder.longIn.assert_any_call(
        "BETA:Bar",
        DESC=mocker.ANY,
        EGU=mocker.ANY,
        LOPR=mocker.ANY,
        HOPR=mocker.ANY,
        initial_value=mocker.ANY,
    )


def test_ca_transport_emits_per_controller_gui_and_docs_files(mocker, tmp_path: Path):
    """#358: GUI/docs emission writes ``output_dir/{id}.bob`` per controller
    plus an index file alongside, even via the transport-level wiring."""
    mocker.patch("fastcs.transports.epics.ca.util.builder")
    mocker.patch("fastcs.transports.epics.ca.ioc.builder")

    api1 = _api_with_id(_OneAttrController, "ALPHA")
    api2 = _api_with_id(_OtherAttrController, "BETA")

    gui_dir = tmp_path / "opis"
    docs_dir = tmp_path / "reference"

    loop = asyncio.new_event_loop()
    try:
        transport = EpicsCATransport(
            gui=EpicsGUIOptions(output_dir=gui_dir),
            docs=EpicsDocsOptions(output_dir=docs_dir),
        )
        transport.connect([api1, api2], loop)
    finally:
        loop.close()

    assert (gui_dir / "ALPHA.bob").is_file()
    assert (gui_dir / "BETA.bob").is_file()
    assert (gui_dir / f"{INDEX_STEM}.bob").is_file()
    assert (docs_dir / "ALPHA.md").is_file()
    assert (docs_dir / "BETA.md").is_file()
    assert (docs_dir / f"{INDEX_STEM}.md").is_file()


def test_ca_transport_rejects_illegal_id_at_connect():
    api = _api_with_id(_OneAttrController, "bad/id")

    loop = asyncio.new_event_loop()
    try:
        transport = EpicsCATransport()
        with pytest.raises(ValueError, match="bad/id"):
            transport.connect([api], loop)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_pva_transport_serves_two_controllers_with_distinct_pvi_roots():
    """One p4p server hosts N controllers; each gets its own ``:PVI`` root with no
    super-parent."""
    api1 = _api_with_id(_OneAttrController, "ALPHA")
    api2 = _api_with_id(_OtherAttrController, "BETA")

    transport = EpicsPVATransport()
    transport.connect([api1, api2], asyncio.get_event_loop())

    providers = transport._ioc._providers
    pv_names = {name for provider in providers for name in provider.keys()}

    assert "ALPHA:PVI" in pv_names
    assert "BETA:PVI" in pv_names
    assert "ALPHA:Foo" in pv_names
    assert "BETA:Bar" in pv_names


def test_pva_transport_rejects_illegal_id_at_connect():
    api = _api_with_id(_OneAttrController, "bad/id")

    loop = asyncio.new_event_loop()
    try:
        transport = EpicsPVATransport()
        with pytest.raises(ValueError, match="bad/id"):
            transport.connect([api], loop)
    finally:
        loop.close()


def test_graphql_transport_combines_two_controllers_under_id_keyed_query():
    """One GraphQL endpoint exposes a single combined schema with one
    top-level Query field per controller id."""
    from fastapi.testclient import TestClient

    api1 = _api_with_id(_OneAttrController, "alpha")
    api2 = _api_with_id(_OtherAttrController, "beta")

    loop = asyncio.new_event_loop()
    try:
        transport = GraphQLTransport()
        transport.connect([api1, api2], loop)

        # Strawberry's ASGI app doesn't implement the lifespan protocol, so
        # construct the TestClient without `with` to skip startup events.
        client = TestClient(transport._server._app)
        response = client.post(
            "/graphql",
            json={"query": "{ alpha { foo } beta { bar } }"},
        )
        assert response.status_code == 200
        assert response.json()["data"] == {
            "alpha": {"foo": 0},
            "beta": {"bar": 0},
        }
    finally:
        loop.close()


def test_graphql_transport_rejects_illegal_id_at_connect():
    # Hyphens are valid for REST/EPICS but not for GraphQL field names.
    api = _api_with_id(_OneAttrController, "bad-id")

    loop = asyncio.new_event_loop()
    try:
        transport = GraphQLTransport()
        with pytest.raises(ValueError, match="bad-id"):
            transport.connect([api], loop)
    finally:
        loop.close()


def test_tango_transport_builds_one_device_per_controller_with_id_in_name():
    """One Tango DSR hosts N devices; each id forms the leading segment of its
    three-segment device name and a unique device class is built per controller."""
    from fastcs.transports.tango.dsr import FASTCS_TANGO_SERVER_NAME
    from fastcs.transports.tango.transport import TangoTransport
    from fastcs.transports.tango.util import tango_dev_class_name, tango_dev_name

    api1 = _api_with_id(_OneAttrController, "ALPHA")
    api2 = _api_with_id(_OtherAttrController, "BETA")

    loop = asyncio.new_event_loop()
    try:
        transport = TangoTransport()
        transport.connect([api1, api2], loop)
    finally:
        loop.close()

    # Two distinct Tango device classes, one per controller, named after the id.
    devices = transport._dsr._devices
    assert len(devices) == 2
    assert [d.__name__ for d in devices] == ["ALPHA", "BETA"]

    # Device names embed the id as the leading segment.
    instance = transport.tango.dsr_instance
    assert tango_dev_name("ALPHA", instance) == f"ALPHA/ALPHA/{instance}"
    assert tango_dev_name("BETA", instance) == f"BETA/BETA/{instance}"

    # FastCS-hosted DSRs use a fixed server name independent of controller class
    # so a multi-class server has a single identity.
    assert FASTCS_TANGO_SERVER_NAME == "FastCS"
    assert tango_dev_class_name("ALPHA") == "ALPHA"
    assert tango_dev_class_name("BETA") == "BETA"


def test_tango_transport_rejects_illegal_id_at_connect():
    api = _api_with_id(_OneAttrController, "bad/id")

    loop = asyncio.new_event_loop()
    try:
        from fastcs.transports.tango.transport import TangoTransport

        transport = TangoTransport()
        with pytest.raises(ValueError, match="bad/id"):
            transport.connect([api], loop)
    finally:
        loop.close()


def test_tango_transport_rejects_post_sanitisation_class_name_collision():
    """#371: hyphens and underscores both sanitise to underscores in Tango device
    class names, so ``DEV-1`` and ``DEV_1`` would silently override each other in
    ``tango.server.run``. Detect and fail fast at connect."""
    api1 = _api_with_id(_OneAttrController, "DEV-1")
    api2 = _api_with_id(_OtherAttrController, "DEV_1")

    loop = asyncio.new_event_loop()
    try:
        from fastcs.transports.tango.transport import TangoTransport

        transport = TangoTransport()
        with pytest.raises(ValueError) as excinfo:
            transport.connect([api1, api2], loop)
    finally:
        loop.close()

    message = str(excinfo.value)
    # Both colliding ids appear in the message so callers can spot the pair.
    assert "'DEV-1'" in message
    assert "'DEV_1'" in message


class _LifecycleController(Controller):
    """Records lifecycle hook calls for end-to-end assertions."""

    foo = AttrR(int)

    def __init__(self):
        super().__init__()
        self.connect_called = False
        self.initialised = False
        self.post_initialised = False

    async def initialise(self):
        self.initialised = True

    def post_initialise(self):
        self.post_initialised = True

    async def connect(self):
        self.connect_called = True

    async def disconnect(self):
        self.connect_called = False


class _OtherLifecycleController(_LifecycleController):
    bar = AttrR(int)


@pytest.mark.asyncio
async def test_fastcs_serves_two_controllers_end_to_end(mocker: MockerFixture):
    """FastCS.serve drives lifecycle on every controller and routes REST traffic
    per-id; combined OpenAPI describes both prefixes."""
    a = _LifecycleController()
    a.set_path(["alpha"])
    b = _OtherLifecycleController()
    b.set_path(["beta"])

    transport = RestTransport()
    # Stop RestTransport from binding to a real port; we exercise the FastAPI
    # app directly through TestClient.
    mocker.patch.object(RestTransport, "serve", new=lambda self: asyncio.sleep(3600))

    fastcs = FastCS([a, b], [transport], asyncio.get_event_loop())
    task = asyncio.create_task(fastcs.serve(interactive=False))
    try:
        await asyncio.sleep(0.1)

        for controller in (a, b):
            assert controller.initialised
            assert controller.post_initialised
            assert controller.connect_called

        with TestClient(transport._server._app) as client:
            assert client.get("/alpha/foo").status_code == 200
            assert client.get("/beta/foo").status_code == 200
            assert client.get("/beta/bar").status_code == 200

            openapi = client.get("/openapi.json").json()
            paths = set(openapi["paths"])
            assert "/alpha/foo" in paths
            assert "/beta/foo" in paths
            assert "/beta/bar" in paths
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for controller in (a, b):
        assert not controller.connect_called
