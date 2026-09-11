import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastcs.connections import (
    Connection,
    Connections,
    DRANode,
    HTTPConnection,
    HTTPConnectionSettings,
    IPConnection,
    IPConnectionSettings,
    Recovery,
    SerialConnection,
    SerialConnectionSettings,
    SimConnection,
)
from fastcs.connections.ip_connection import DisconnectedError, StreamConnection
from fastcs.connections.serial_connection import NotOpenedError


class OneConnection(Connection):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class AnotherConnection(Connection):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


# Connections registry


def test_a_connection_is_claimed_by_name_with_its_type_asserted():
    connection = OneConnection()
    registry = Connections({"one": connection})

    assert registry.get("one", OneConnection) is connection


def test_claiming_a_name_that_was_not_declared_lists_the_ones_that_were():
    registry = Connections({"one": OneConnection(), "two": AnotherConnection()})

    with pytest.raises(KeyError, match=r"No connection named 'three'") as exc:
        registry.get("three", OneConnection)

    assert "'one', 'two'" in str(exc.value)


def test_claiming_a_name_with_the_wrong_type_says_both_types():
    registry = Connections({"one": AnotherConnection()})

    with pytest.raises(TypeError, match="is AnotherConnection, but OneConnection"):
        registry.get("one", OneConnection)


def test_a_registry_reports_what_was_never_claimed():
    registry = Connections({"used": OneConnection(), "spare": OneConnection()})

    assert registry.unclaimed() == {"used", "spare"}

    registry.get("used", OneConnection)

    assert registry.unclaimed() == {"spare"}


def test_a_connection_is_named_by_identity_not_equality():
    """Two connections with matching settings are two connections."""
    first, second = OneConnection(), OneConnection()
    registry = Connections({"first": first, "second": second})

    assert registry.name_of(first) == "first"
    assert registry.name_of(second) == "second"
    assert registry.name_of(OneConnection()) is None


def test_a_registry_keeps_declaration_order():
    first, second = OneConnection(), AnotherConnection()
    registry = Connections({"first": first, "second": second})

    assert registry.values() == [first, second]
    assert len(registry) == 2
    assert "first" in registry
    assert "third" not in registry
    assert repr(registry) == "Connections(['first', 'second'])"


# IPConnection


@pytest.mark.asyncio
async def test_ip_connect_opens_the_settings_it_was_given():
    connection = IPConnection(IPConnectionSettings(ip="192.0.2.1", port=1234))
    reader, writer = MagicMock(), MagicMock()

    with patch(
        "asyncio.open_connection", AsyncMock(return_value=(reader, writer))
    ) as open_connection:
        await connection.connect()

    open_connection.assert_awaited_once_with("192.0.2.1", 1234)
    assert isinstance(connection._connection, StreamConnection)  # noqa: SLF001


@pytest.mark.asyncio
async def test_using_an_unopened_ip_connection_says_so():
    with pytest.raises(DisconnectedError, match="call connect"):
        await IPConnection().send_command("ID?\r\n")


@pytest.mark.asyncio
async def test_a_command_that_hits_a_dead_socket_marks_the_link_down():
    connection = IPConnection()
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.send_message = AsyncMock(side_effect=ConnectionResetError)
    connection._IPConnection__connection = stream  # pyright: ignore[reportAttributeAccessIssue]
    connection._set_connected()  # noqa: SLF001

    with pytest.raises(ConnectionResetError):
        await connection.send_command("R=1\r\n")

    assert not connection.connected


@pytest.mark.asyncio
async def test_a_command_the_device_accepts_leaves_the_link_up():
    connection = IPConnection()
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.send_message = AsyncMock()
    connection._IPConnection__connection = stream  # pyright: ignore[reportAttributeAccessIssue]
    connection._set_connected()  # noqa: SLF001

    await connection.send_command("R=1\r\n")

    stream.send_message.assert_awaited_once_with("R=1\r\n")
    assert connection.connected


@pytest.mark.asyncio
async def test_stream_connection_reads_and_writes_lines():
    reader = asyncio.StreamReader()
    reader.feed_data(b"ID=1\r\n")
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()

    stream = StreamConnection(reader, writer)
    async with stream as held:
        await held.send_message("ID?\r\n")
        assert await held.receive_response() == "ID=1\r\n"

    writer.write.assert_called_once_with(b"ID?\r\n")

    await stream.close()
    writer.close.assert_called_once()


# SerialConnection


@pytest.mark.asyncio
async def test_serial_connect_opens_the_settings_it_was_given():
    connection = SerialConnection(
        SerialConnectionSettings(port="/dev/ttyS0", baud=9600)
    )

    with patch("aioserial.AioSerial") as aioserial:
        await connection.connect()

    aioserial.assert_called_once_with(port="/dev/ttyS0", baudrate=9600)


@pytest.mark.asyncio
async def test_using_an_unopened_serial_connection_says_so():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyS0"))

    with pytest.raises(NotOpenedError, match="call connect"):
        await connection.send_command(b"ID?\r\n")


@pytest.mark.asyncio
async def test_serial_round_trip_leaves_the_link_up():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyS0"))
    stream = MagicMock()
    stream.write_async = AsyncMock()
    stream.read_async = AsyncMock(return_value=b"ID=1")

    with patch("aioserial.AioSerial", return_value=stream):
        await connection.connect()
    connection._set_connected()  # noqa: SLF001

    await connection.send_command(b"R=1\r\n")
    assert await connection.send_query(b"ID?\r\n", 4) == b"ID=1"
    assert connection.connected

    await connection.close()
    stream.close.assert_called_once()
    # Closing an already-closed link is tolerated - the runner does it before
    # every reconnect attempt.
    await connection.close()


@pytest.mark.asyncio
async def test_a_serial_port_that_goes_away_marks_the_link_down():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyS0"))
    stream = MagicMock()
    stream.write_async = AsyncMock(side_effect=OSError)
    stream.read_async = AsyncMock(side_effect=OSError)

    with patch("aioserial.AioSerial", return_value=stream):
        await connection.connect()
    connection._set_connected()  # noqa: SLF001

    with pytest.raises(OSError):
        await connection.send_command(b"R=1\r\n")
    assert not connection.connected

    connection._set_connected()  # noqa: SLF001
    stream.write_async = AsyncMock()
    with pytest.raises(OSError):
        await connection.send_query(b"ID?\r\n", 4)
    assert not connection.connected


# SimConnection


@pytest.mark.asyncio
async def test_a_sim_connection_opens_and_closes_without_a_transport():
    """The pretending is all a driver writes: there is nothing here to fail."""

    class SimDevice(SimConnection):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.position = 0

        async def move(self, steps: int) -> None:
            self.position += steps

    connection = SimDevice()
    await connection.connect()
    connection._set_connected()  # noqa: SLF001

    await connection.move(3)
    assert connection.position == 3
    assert connection.connected

    await connection.close()


@pytest.mark.asyncio
async def test_a_sim_connection_is_a_sibling_of_the_real_transports():
    """Not a subclass of one: it would inherit a handle it never opens.

    It is a `Connection` like any other, so it takes the same reconnect settings
    and is chosen by ``type:`` in the same place - even though its reconnect task
    will idle forever.
    """
    assert issubclass(SimConnection, Connection)
    assert not issubclass(SimConnection, IPConnection | SerialConnection)

    connection = SimConnection.__new__(SimConnection)
    Connection.__init__(connection, reconnect_period=2.0)
    assert connection.reconnect_period == 2.0


# Recovery


def test_a_missing_device_node_is_terminal_for_a_dra_node():
    assert DRANode().is_terminal(FileNotFoundError())


def test_other_failures_are_not_terminal_for_a_dra_node():
    assert not DRANode().is_terminal(TimeoutError())
    assert not DRANode().is_terminal(OSError("I/O error"))


def test_the_default_policy_never_gives_up_early():
    assert not Recovery().is_terminal(FileNotFoundError())
    assert not Recovery.is_fatal


def test_a_dra_node_is_fatal():
    """Only a pod restart can re-establish the claim, so it asks for one."""
    assert DRANode.is_fatal


def test_every_connection_keeps_retrying_by_default():
    assert isinstance(OneConnection().recovery, Recovery)
    assert not OneConnection().recovery.is_terminal(FileNotFoundError())


def test_one_policy_serves_any_transport():
    """No class per transport × policy: the same instance is held by both."""
    policy = DRANode()
    serial = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))
    ip = IPConnection(IPConnectionSettings(ip="192.0.2.1", port=1234))
    serial.recovery = policy
    ip.recovery = policy

    assert serial.recovery.is_terminal(FileNotFoundError())
    assert ip.recovery.is_terminal(FileNotFoundError())
    assert "/dev/ttyACM0" in serial.recovery.reason(serial)
    assert "192.0.2.1:1234" in ip.recovery.reason(ip)


def test_assigning_a_policy_to_one_instance_changes_only_that_instance():
    claimed = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))
    unclaimed = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM1"))

    claimed.recovery = DRANode()

    assert claimed.recovery.is_terminal(FileNotFoundError())
    assert not unclaimed.recovery.is_terminal(FileNotFoundError())


def test_a_policy_can_be_set_on_the_class():
    class DRASerialConnection(SerialConnection):
        recovery = DRANode()

    connection = DRASerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))

    assert connection.recovery.is_terminal(FileNotFoundError())


def test_the_default_reason_names_the_device():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))

    assert Recovery().reason(connection) == (
        "/dev/ttyACM0 cannot recover from this failure."
    )


# label


def test_a_connection_is_labelled_by_its_class_unless_it_knows_its_device():
    assert OneConnection().label == "OneConnection"


def test_a_serial_connection_is_labelled_by_its_port():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))

    assert connection.label == "/dev/ttyACM0"


def test_an_ip_connection_is_labelled_by_its_address():
    connection = IPConnection(IPConnectionSettings(ip="192.0.2.1", port=1234))

    assert connection.label == "192.0.2.1:1234"


def test_an_http_connection_is_labelled_by_its_base_url():
    connection = HTTPConnection(HTTPConnectionSettings(host="192.0.2.1", port=8080))

    assert connection.label == "http://192.0.2.1:8080"
