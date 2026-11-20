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
socket or a serial adapter being unplugged, the exchange raises `ConnectionError`.

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
    disconnect_after_timeouts = 3
    ...

    @disconnect_on(ConnectionError, TimeoutError)
    async def get(self, key: str) -> Any:
        return await self.client.get(f"/detector/{key}")
```

If the method raises a `DisconnectedError` or one of the listed exceptions, the
connection is marked down and the exception carries on to the caller. Bare
`@disconnect_on` only lists `DisconnectedError`, so list `ConnectionError` too if it
means the link has gone.

If `TimeoutError` is listed, directly or through a base class like `OSError`, timeouts
are counted: the connection is marked down after `disconnect_after_timeouts` in a row,
1 by default, and any call that works starts the count again.

While it's down, decorated methods raise `DisconnectedError` without touching the
device, except when called from the connection's own `connect()` or `introspect()` while
FastCS reconnects it. Other exceptions, like an error reply from the device, leave the
link alone. The built-in connections already decorate their IO methods.

When a connection is marked down, FastCS:

1. Sets `connected` to `False` on every controller holding it, and pauses their scans.
2. Waits for the connections it `depends_on` to be up, then calls `close()`, `connect()`
   and `introspect()`. The first try is straight away, and later ones are
   `reconnect_period` seconds apart.
3. Once that works and the introspection's structure is unchanged, marks the connection
   up, sets `connected` back to `True`, re-runs the controllers' `ONCE` scans and
   resumes the others.
4. After `reconnect_attempts` failed tries, stops with a `ReconnectFailedError`.

If the new introspection has different fields or keys from the one the controllers were
built from, FastCS stops with an `IntrospectionMismatchError`. Changed values are fine.

Both are a `FastCSError`: FastCS shuts down, so that it can be restarted and build its
controllers again.

### Reconnect settings

Each connection's block in `fastcs.yaml` can set how it reconnects, and how many
timeouts mean it has gone:

```yaml
connections:
  connection:
    ip: 10.0.0.5
    port: 4000
    reconnect_attempts: 5
    reconnect_period: 2.0
    disconnect_after_timeouts: 1
```

`reconnect_attempts: 0` or `null` means stop as soon as the connection goes down. That
is the default for a connection class that doesn't set its own, which it does with:

```python
class EigerConnection(Connection):
    reconnect_attempts = 20
    reconnect_period = 3.0
```

A class that sets `reconnect_attempts` must set `reconnect_period` too, or defining it
raises a `TypeError`. In Python, set them on one connection with `set_reconnect`, which
returns the connection. `disconnect_after_timeouts` is optional, and keeps the class's
value if it's left out:

```python
connection = IPConnection("10.0.0.5", 4000).set_reconnect(
    attempts=5, period=2.0, disconnect_after_timeouts=3
)
```

| Connection | `reconnect_attempts` | `reconnect_period` |
|---|---|---|
| `Connection` | `None` | `None` |
| `IPConnection` | 30 | 2.0 |
| `HTTPConnection` | 60 | 5.0 |
| `SerialConnection` | 10 | 1.0 |

`disconnect_after_timeouts` is 1 unless a class sets it, and must be at least 1. It only
applies to methods whose `@disconnect_on` lists `TimeoutError`, so `HTTPConnection`'s
timeouts never mark it down. `IPConnection` and `SerialConnection` close the link on a
timeout, so a late reply isn't read as the reply to the next exchange, and a higher
value only delays marking them down until the next call.

### Which controllers a connection belongs to

FastCS finds the controllers to mark down by the connection each one is given, so:

- The controller that uses a connection is given it, as
  `super().__init__(connection=connection)`, and passes it to any sub controllers that
  need it, which are given it too.
- Each controller holds at most one connection.
- Every connection a controller holds was opened by FastCS, from the top-level
  controller's arguments or discovered for them.

FastCS raises a `ValueError` at startup if any of these is broken.

## Discovered connections

Some devices report where other links are, like Odin reporting the port of its meta
writer. `introspect()` can return a `ConnectionSpec` for each of these alongside the
description, and the connection's class declares each one by name:

```python
from fastcs.connections import Connection, ConnectionSpec, Introspection


class OdinConnection(Connection[OdinIntrospection]):
    meta: MetaWriterConnection

    async def introspect(self) -> Introspection[OdinIntrospection]:
        info = OdinIntrospection(await self.get("/api/tree"))
        meta = ConnectionSpec("meta", MetaWriterConnection, port=info.meta_port)
        return Introspection(info, subconnections=[meta])
```

FastCS opens each one and sets it as the attribute of that name on the connection that
discovered it, here `odin.meta`, which a controller passes on to the sub controller
that uses it:

```python
class OdinController(Controller[OdinConnection]):
    def __init__(self, connection: OdinConnection) -> None:
        super().__init__(connection=connection)
        self.META = MetaWriterController(connection=connection.meta)
```

- It's opened after the connections that are given. It can discover connections of
  its own, set on it the same way, e.g. `odin.meta.x`.
- FastCS raises a `ValueError` if `introspect()` returns one its class doesn't declare,
  or leaves out one it declares, unless that is hinted `X | None`, which is set to
  `None`.
- It's independent of the connection that discovered it, so it only goes down when its
  own IO fails. Set `depends_on` on its class if it needs its parent to be up first.
- It's opened after the connections that are given, so none of those can depend on
  it. If a given connection's `depends_on` matches the type of a subconnection that is
  declared, FastCS raises a `ValueError` before opening anything.
- It uses its class's reconnect settings.
- If a reconnect discovers a different spec, such as a new port, FastCS stops with an
  `IntrospectionMismatchError`.
