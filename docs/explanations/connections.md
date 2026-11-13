# Connections

A `Connection` is a link to a device. A controller takes its connections as constructor
arguments. FastCS opens them before constructing the controller, and closes them when
the application stops.

## Built-in connections

| Connection | Link | Install |
|---|---|---|
| `IPConnection` | TCP | included |
| `SerialConnection` | Serial port | `pip install "fastcs[serial]"` |
| `HTTPConnection` | HTTP | `pip install "fastcs[http]"` |

`IPConnection` and `SerialConnection` exchange text messages that end with a
terminator, `\r\n` by default:

```python
connection = IPConnection("10.0.0.5", 4000, terminator="\r\n", timeout=5.0)

await connection.write("R=5")  # sends "R=5\r\n"
reply = await connection.query("R?")  # sends "R?\r\n", returns the reply without "\r\n"
```

For binary protocols, `write_bytes` and `query_bytes` send raw bytes without a
terminator. If an exchange fails or takes longer than `timeout`, the link is closed and
later calls raise `DisconnectedError`. If the link fails, such as the device closing the
socket or a serial adapter being unplugged, the exchange raises `ConnectionError`, which
`DisconnectedError` subclasses, so
`except (ConnectionError, TimeoutError)` catches any link failure.

`HTTPConnection` talks to a device with a JSON API. `get`, `put`, `post` and `delete`
take a path relative to its `url`, send JSON, and return the parsed reply. An error
status raises `httpx.HTTPStatusError`. For replies that aren't JSON, `request` returns
the raw response:

```python
connection = HTTPConnection("http://10.0.0.5:8000/api", timeout=5.0)

await connection.put("/rate", json=5)
rate = await connection.get("/rate")
```

Like the other connections, it raises `TimeoutError` when the device doesn't answer in
time, but leaves the link open, as one slow request doesn't mean the server has gone. It
raises `ConnectionError` when the server can't be reached, and closes the link so later
calls raise `DisconnectedError`.
