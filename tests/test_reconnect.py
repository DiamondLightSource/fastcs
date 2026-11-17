import asyncio
from collections.abc import Callable

import pytest

from fastcs.connections import (
    Connection,
    DisconnectedError,
    IPConnection,
    disconnect_on,
)
from fastcs.connections.connection import _structure
from fastcs.controllers import Controller, ControllerSpec
from fastcs.exceptions import IntrospectionMismatchError, ReconnectFailedError
from fastcs.methods import scan
from fastcs.runner import ControllerRunner
from tests.example_connections import (
    DeviceIntrospection,
    EigerConnection,
    HoldingController,
    IntrospectedController,
    IntrospectingConnection,
    OdinConnection,
    RecordingConnection,
    TwoConnectionController,
)


async def _wait_until(condition: Callable[[], bool], timeout: float = 1) -> None:
    """Wait until ``condition`` is true, checking every 10ms"""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def test_structure_counts_fields_and_keys_but_not_values():
    assert _structure(DeviceIntrospection({"gain": 2})) == _structure(
        DeviceIntrospection({"gain": 3})
    )
    assert _structure(DeviceIntrospection({"gain": 2})) != _structure(
        DeviceIntrospection({"offset": 2})
    )


class ChangingConnection(IntrospectingConnection):
    def __init__(self) -> None:
        self.introspections = 0

    async def introspect(self) -> DeviceIntrospection:
        self.introspections += 1
        if self.introspections == 1:
            return DeviceIntrospection({"gain": 2})
        return DeviceIntrospection({"offset": 2})


@pytest.mark.asyncio
async def test_introspection_changing_structure_on_reconnect_is_fatal():
    connection = ChangingConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", IntrospectedController, connection)]
    )
    await runner.start()

    connection.state.mark_down()
    with pytest.raises(IntrospectionMismatchError):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)
    await runner.stop()


class PoppingController(Controller):
    def __init__(self, connection: IntrospectingConnection) -> None:
        super().__init__()
        self.connection = connection
        connection.introspection.parameters.pop("gain")


@pytest.mark.asyncio
async def test_controller_mutating_introspection_still_reconnects():
    connection = IntrospectingConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", PoppingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]

    connection.state.mark_down()
    await controller.connected.wait_for_value(False, timeout=1)
    await controller.connected.wait_for_value(True, timeout=1)
    await runner.stop()


class FlakyConnection(Connection):
    reconnect_attempts = 10
    reconnect_period = 0

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        if self.connects > 1 and self.failures:
            self.failures -= 1
            raise ConnectionError("Device is off")

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_watcher_reconnects_then_marks_controllers_up():
    connection = FlakyConnection(failures=1)
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]
    assert isinstance(controller, HoldingController)

    connection.state.mark_down()
    await controller.connected.wait_for_value(False, timeout=1)
    await controller.connected.wait_for_value(True, timeout=1)
    await _wait_until(lambda: controller.reads == 2)

    assert connection.connects == 3
    await runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [None, 2])
async def test_watcher_raises_when_reconnect_attempts_run_out(attempts: int | None):
    connection = FlakyConnection(failures=attempts or 0)
    connection.reconnect_attempts = attempts
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]

    connection.state.mark_down()
    with pytest.raises(ReconnectFailedError):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)

    assert connection.connects == 1 + (attempts or 0)
    assert controller.connected.get() is False
    assert not runner._scan_gates[id(controller)].is_set()
    await runner.stop()


@pytest.mark.asyncio
async def test_connection_going_down_marks_only_its_holders_disconnected():
    class Parent(Controller):
        def __init__(self, connection: Connection) -> None:
            super().__init__()
            self.A = HoldingController(connection)
            self.B = HoldingController(connection)

    connection = RecordingConnection([])
    connection.reconnect_attempts = None
    runner = await ControllerRunner.build([ControllerSpec("P", Parent, connection)])
    await runner.start()
    parent = runner.controllers[0]

    connection.state.mark_down()
    with pytest.raises(ReconnectFailedError):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)

    holders = parent.sub_controllers.values()
    assert [holder.connected.get() for holder in holders] == [False, False]
    assert parent.connected.get() is True
    await runner.stop()


class ScanningController(HoldingController):
    def __init__(self, connection: Connection) -> None:
        super().__init__(connection)
        self.scans = 0

    @scan(0.01)
    async def count(self) -> None:
        self.scans += 1


@pytest.mark.asyncio
async def test_connection_going_down_pauses_only_its_controllers_scans():
    down, up = RecordingConnection([]), RecordingConnection([])
    down.reconnect_attempts = None
    runner = await ControllerRunner.build(
        [
            ControllerSpec("A", ScanningController, down),
            ControllerSpec("B", ScanningController, up),
        ]
    )
    await runner.start()
    paused, running = runner.controllers
    assert isinstance(paused, ScanningController)
    assert isinstance(running, ScanningController)

    down.state.mark_down()
    with pytest.raises(ReconnectFailedError):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)

    # Same period, so two cycles of the other let a paused cycle under way finish
    target = running.scans + 2
    await _wait_until(lambda: running.scans >= target)
    paused_scans = paused.scans
    target = running.scans + 3
    await _wait_until(lambda: running.scans >= target)
    assert paused.scans == paused_scans
    await runner.stop()


@pytest.mark.asyncio
async def test_watcher_raises_when_instance_has_attempts_but_no_period():
    connection = FlakyConnection(failures=0)
    connection.reconnect_period = None
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()

    connection.state.mark_down()
    with pytest.raises(ReconnectFailedError, match="no reconnect_period"):
        await asyncio.wait_for(runner.wait_for_fatal_error(), timeout=1)
    await runner.stop()


class HeldEigerConnection(EigerConnection):
    def __init__(self, events: list[str], name: str = "") -> None:
        super().__init__(events, name)
        self.connecting = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def connect(self) -> None:
        self.connecting.set()
        await self.release.wait()
        await super().connect()


@pytest.mark.asyncio
async def test_reconnect_waits_for_dependencies_to_be_up():
    events: list[str] = []
    eiger = HeldEigerConnection(events, "eiger")
    odin = OdinConnection(events, "odin")
    runner = await ControllerRunner.build(
        [ControllerSpec("A", TwoConnectionController, odin=odin, eiger=eiger)]
    )
    await runner.start()
    events.clear()
    eiger.connecting.clear()
    eiger.release.clear()

    eiger.state.mark_down()
    odin.state.mark_down()
    await asyncio.wait_for(eiger.connecting.wait(), timeout=1)
    assert events == ["close eiger"]

    eiger.release.set()
    await asyncio.wait_for(odin.state.wait_up(), timeout=1)
    assert events == ["close eiger", "connect eiger", "close odin", "connect odin"]
    await runner.stop()


class HandshakeConnection(Connection):
    reconnect_attempts = 10
    reconnect_period = 0

    def __init__(self) -> None:
        self.handshakes = 0
        self.introspecting = asyncio.Event()
        self.release = asyncio.Event()

    async def connect(self) -> None:
        await self.handshake()

    @disconnect_on
    async def handshake(self) -> None:
        self.handshakes += 1

    async def introspect(self) -> None:
        if self.handshakes > 1:
            self.introspecting.set()
            await self.release.wait()

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_only_the_reconnect_can_use_a_down_connection():
    connection = HandshakeConnection()
    runner = await ControllerRunner.build(
        [ControllerSpec("A", HoldingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]

    connection.state.mark_down()
    await asyncio.wait_for(connection.introspecting.wait(), timeout=1)
    with pytest.raises(DisconnectedError):
        await connection.handshake()

    connection.release.set()
    await controller.connected.wait_for_value(True, timeout=1)
    assert connection.handshakes == 2
    await runner.stop()


class _Device:
    """A TCP device that replies to ``R?``, and can be switched off and on"""

    def __init__(self) -> None:
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def on(self) -> None:
        self._server = await asyncio.start_server(self._reply, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def off(self) -> None:
        assert self._server is not None
        self._server.close()
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()

    async def _reply(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._writers.add(writer)
        try:
            while await reader.readuntil(b"\r\n"):
                writer.write(b"5\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)


class PollingController(Controller):
    def __init__(self, connection: IPConnection) -> None:
        super().__init__()
        self.connection = connection
        self.polls = 0

    @scan(0.01)
    async def poll(self) -> None:
        await self.connection.query("R?")
        self.polls += 1


@pytest.mark.asyncio
async def test_reconnects_after_device_restart():
    device = _Device()
    await device.on()
    connection = IPConnection("127.0.0.1", device.port, timeout=0.5)
    connection.reconnect_period = 0.01
    runner = await ControllerRunner.build(
        [ControllerSpec("A", PollingController, connection)]
    )
    await runner.start()
    controller = runner.controllers[0]
    assert isinstance(controller, PollingController)
    await _wait_until(lambda: controller.polls >= 1)

    await device.off()
    await controller.connected.wait_for_value(False, timeout=1)

    polls = controller.polls
    await device.on()
    await controller.connected.wait_for_value(True, timeout=1)
    # A failed scan cycle waits a second before the next one
    await _wait_until(lambda: controller.polls > polls, timeout=3)
    await runner.stop()
    await device.off()
