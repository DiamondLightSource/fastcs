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

## When the link goes down

A connection marks the IO methods whose failure means the link has gone with
`@disconnect_on`:

```python
from fastcs.connections import Connection, disconnect_on


class EigerConnection(Connection):
    ...

    @disconnect_on(TimeoutError)
    async def get(self, key: str) -> Any:
        return await self.client.get(f"/detector/{key}")
```

If the method raises a `ConnectionError`, a `DisconnectedError` or one of the listed
exceptions, the connection is marked down and the exception carries on to the caller.
While it's down, decorated methods raise `DisconnectedError` without touching the
device. Other exceptions, like an error reply from the device, leave the link alone. The
built-in connections already decorate their IO methods.

When a connection is marked down, FastCS:

1. Sets `connected` to `False` on every controller holding it, and pauses their scans.
2. Waits for the connections it `depends_on` to be up, then calls `close()`, `connect()`
   and `introspect()`. The first try is straight away, and later ones are
   `reconnect_period` seconds apart.
3. Once that works, sets `connected` back to `True`, re-runs the controllers' `ONCE`
   scans and resumes the others.
4. After `reconnect_attempts` failed tries, gives up. The controllers stay disconnected
   while the rest of the application carries on.

If the new introspection has different fields or keys from the one the controllers were
built from, FastCS stops with a `FatalError`, so that it can be restarted and build them
again. Changed values are fine.

### Reconnect settings

Each connection's block in `fastcs.yaml` can set how it reconnects:

```yaml
connections:
  connection:
    ip: 10.0.0.5
    port: 4000
    reconnect_attempts: 5
    reconnect_period: 2.0
```

`reconnect_attempts: 0` means never reconnect. A connection class can set its own
defaults:

```python
class EigerConnection(Connection):
    reconnect_attempts = 20
    reconnect_period = 3.0
```

| Connection | `reconnect_attempts` | `reconnect_period` |
|---|---|---|
| `Connection` | 10 | 5.0 |
| `IPConnection` | 30 | 2.0 |
| `HTTPConnection` | 60 | 5.0 |
| `SerialConnection` | 10 | 1.0 |

### Which controllers a connection belongs to

FastCS finds the controllers to mark down by looking for the connection on them, so:

- The controller that uses a connection stores it, e.g. `self.connection = connection`,
  and passes it to any sub controllers that need it, which store it too.
- Each controller stores at most one connection.
- Every connection a controller stores was opened by FastCS, from the top-level
  controller's arguments.

FastCS raises a `ValueError` at startup if any of these is broken.
