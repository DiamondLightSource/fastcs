import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio

from fastcs.connections import DisconnectedError, HTTPConnection


class _RateDevice(BaseHTTPRequestHandler):
    rate = 0

    def do_GET(self):
        if self.path != "/rate":
            self.send_error(404)
            return

        self._reply(type(self).rate)

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).rate = json.loads(body)
        self._reply(None)

    def do_POST(self):
        self.do_PUT()

    def do_DELETE(self):
        type(self).rate = 0
        self._reply(None)

    def _reply(self, value):
        body = b"" if value is None else json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server():
    _RateDevice.rate = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RateDevice)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture
async def connection(server: ThreadingHTTPServer):
    # The probe in connect() gets a 404 for "/", which still counts as reachable
    connection = HTTPConnection(f"http://127.0.0.1:{server.server_port}", timeout=1)
    await connection.connect()
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_put_then_get_json(connection: HTTPConnection):
    await connection.put("/rate", json=5)
    assert await connection.get("/rate") == 5


@pytest.mark.asyncio
async def test_connect_when_connected_raises(connection: HTTPConnection):
    with pytest.raises(RuntimeError, match="already connected"):
        await connection.connect()


@pytest.mark.asyncio
async def test_link_failure_closes_connection(
    server: ThreadingHTTPServer, connection: HTTPConnection
):
    server.shutdown()
    server.server_close()

    with pytest.raises(ConnectionError):
        await connection.get("/rate")
    with pytest.raises(DisconnectedError):
        await connection.get("/rate")


@pytest.mark.asyncio
async def test_connect_to_closed_port_raises_connection_error():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]

    with pytest.raises(ConnectionError):
        await HTTPConnection(f"http://127.0.0.1:{port}").connect()


@pytest.mark.asyncio
async def test_post_delete_and_request(connection: HTTPConnection):
    await connection.post("/rate", json=7)
    assert (await connection.request("GET", "/rate")).json() == 7

    await connection.delete("/rate")
    assert await connection.get("/rate") == 0


@pytest.mark.asyncio
async def test_unanswered_and_unconnected_requests_raise():
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        connection = HTTPConnection(
            f"http://127.0.0.1:{silent.getsockname()[1]}", timeout=0.1
        )
        with pytest.raises(TimeoutError):
            await connection.connect()

    await connection.close()
    with pytest.raises(DisconnectedError):
        await connection.get("/rate")


@pytest.mark.asyncio
async def test_slowly_dripped_reply_times_out():
    async def drip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\n")
        while True:
            writer.write(b"X")
            await writer.drain()
            await asyncio.sleep(0.02)

    server = await asyncio.start_server(drip, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    connection = HTTPConnection(f"http://127.0.0.1:{port}", timeout=0.2)

    # Guard with asyncio.wait, which doesn't raise TimeoutError itself
    task = asyncio.create_task(connection.connect())
    await asyncio.wait([task], timeout=2)
    server.close()

    assert task.done()
    assert isinstance(task.exception(), TimeoutError)


@pytest.mark.asyncio
async def test_missing_ca_bundle_raises():
    connection = HTTPConnection("https://127.0.0.1", verify="/no/such/bundle.pem")
    with pytest.raises(FileNotFoundError):
        await connection.connect()
