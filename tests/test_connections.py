import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fastcs.connections import (
    Connection,
    ConnectionPolicy,
    DisconnectedError,
    DRAPolicy,
    HTTPConnection,
    HTTPConnectionSettings,
    IPConnection,
    IPConnectionSettings,
    SerialConnection,
    SerialConnectionSettings,
    SimConnection,
)
from fastcs.connections.ip_connection import StreamConnection
from fastcs.connections.policy import Failure
from fastcs.connections.serial_connection import NotOpenedError


class OneConnection(Connection):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


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
async def test_a_command_that_hits_a_dead_socket_just_raises():
    """No error handling in the IO: the supervisor's boundary reads the exception."""
    connection = IPConnection()
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.send_message = AsyncMock(side_effect=ConnectionResetError)
    connection._IPConnection__connection = stream  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(ConnectionResetError):
        await connection.send_command("R=1\r\n")


@pytest.mark.asyncio
async def test_a_command_is_sent_on_the_stream():
    connection = IPConnection()
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.send_message = AsyncMock()
    connection._IPConnection__connection = stream  # pyright: ignore[reportAttributeAccessIssue]

    await connection.send_command("R=1\r\n")

    stream.send_message.assert_awaited_once_with("R=1\r\n")


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
async def test_serial_round_trip():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyS0"))
    stream = MagicMock()
    stream.write_async = AsyncMock()
    stream.read_async = AsyncMock(return_value=b"ID=1")

    with patch("aioserial.AioSerial", return_value=stream):
        await connection.connect()

    await connection.send_command(b"R=1\r\n")
    assert await connection.send_query(b"ID?\r\n", 4) == b"ID=1"

    await connection.close()
    stream.close.assert_called_once()
    # Closing an already-closed link is tolerated - the runner does it before
    # every reconnect attempt.
    await connection.close()


# SimConnection


@pytest.mark.asyncio
async def test_a_sim_connection_opens_and_closes_without_a_transport():
    """The pretending is all a driver writes: there is nothing here to fail."""

    class SimDevice(SimConnection):
        def __init__(self) -> None:
            self.position = 0

        async def move(self, steps: int) -> None:
            self.position += steps

    connection = SimDevice()
    await connection.connect()

    await connection.move(3)
    assert connection.position == 3

    await connection.close()


def test_a_sim_connection_is_a_sibling_of_the_real_transports():
    """Not a subclass of one: it would inherit a handle it never opens."""
    assert issubclass(SimConnection, Connection)
    assert not issubclass(SimConnection, IPConnection | SerialConnection)


# ConnectionPolicy


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(),
        BrokenPipeError(),
        ConnectionRefusedError(),
        OSError("Network is unreachable"),
        EOFError(),
        asyncio.IncompleteReadError(b"", 4),
        FileNotFoundError("/dev/ttyACM0"),
        httpx.ConnectError("refused"),
        DisconnectedError("the device said it is offline"),
    ],
)
def test_a_link_failure_is_a_disconnection(error: Exception):
    assert ConnectionPolicy().classify(error) is Failure.DISCONNECTED


@pytest.mark.parametrize(
    "error", [TimeoutError(), TimeoutError(), httpx.ReadTimeout("slow")]
)
def test_a_timeout_is_counted_rather_than_a_disconnection(error: Exception):
    assert ConnectionPolicy().classify(error) is Failure.TIMEOUT


@pytest.mark.parametrize(
    "error",
    [
        ValueError("could not convert string to float: 'ERR'"),
        httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "http://device"),
            response=httpx.Response(404),
        ),
        RuntimeError("rejected"),
    ],
)
def test_a_device_error_is_passed_to_the_caller(error: Exception):
    assert ConnectionPolicy().classify(error) is Failure.DEVICE


def test_three_timeouts_in_a_row_mean_disconnected_by_default():
    assert ConnectionPolicy().timeout_count == 3


def test_a_missing_device_node_is_terminal_for_a_dra_device():
    assert DRAPolicy().is_terminal(FileNotFoundError())


@pytest.mark.parametrize("error", [TimeoutError(), OSError("I/O error")])
def test_other_failures_are_not_terminal_for_a_dra_device(error: Exception):
    assert not DRAPolicy().is_terminal(error)


def test_the_default_policy_never_gives_up_early():
    assert not ConnectionPolicy().is_terminal(FileNotFoundError())


def test_the_default_policy_is_not_fatal():
    assert not ConnectionPolicy().fatal


def test_a_dra_device_is_fatal():
    """Only a pod restart can re-establish the claim, so it asks for one."""
    assert DRAPolicy().fatal


def test_a_policy_is_a_set_of_independent_settings():
    """A slow DRA device is a DRAPolicy with a different count, not a new class."""
    policy = DRAPolicy(timeout_count=10)

    assert policy.timeout_count == 10
    assert policy.is_terminal(FileNotFoundError())


def test_every_connection_keeps_retrying_by_default():
    assert not OneConnection.policy.is_terminal(FileNotFoundError())


def test_a_policy_is_set_on_the_class():
    class DRASerialConnection(SerialConnection):
        policy = DRAPolicy()

    assert DRASerialConnection.policy.is_terminal(FileNotFoundError())


def test_the_default_reason_names_the_device():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))

    assert ConnectionPolicy().reason(connection) == (
        "/dev/ttyACM0 cannot recover from this failure."
    )


def test_the_dra_reason_names_the_device_node():
    connection = SerialConnection(SerialConnectionSettings(port="/dev/ttyACM0"))

    assert "/dev/ttyACM0" in DRAPolicy().reason(connection)


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
