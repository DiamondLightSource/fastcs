import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastcs.connections import (
    Connection,
    Connections,
    IPConnection,
    IPConnectionSettings,
    SerialConnection,
    SerialConnectionSettings,
)
from fastcs.connections.ip_connection import DisconnectedError, StreamConnection
from fastcs.connections.serial_connection import NotOpenedError


class OneConnection(Connection[None]):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class AnotherConnection(Connection[None]):
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
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyS0", baud=9600))

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
