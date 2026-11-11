# Connections

A `Connection` is a link to a device. A controller takes its connections as constructor
arguments. FastCS opens them before constructing the controller, and closes them when
the application stops.

## Built-in connections

| Connection | Link | Install |
|---|---|---|
| `IPConnection` | TCP | included |
| `SerialConnection` | Serial port | `pip install "fastcs[serial]"` |

Both exchange text messages that end with a terminator, `\r\n` by default:

```python
connection = IPConnection("10.0.0.5", 4000, terminator="\r\n", timeout=5.0)

await connection.write("R=5")  # sends "R=5\r\n"
reply = await connection.query("R?")  # sends "R?\r\n", returns the reply without "\r\n"
```

For binary protocols, `write_bytes` and `query_bytes` send raw bytes without a
terminator. If an exchange fails or takes longer than `timeout`, the link is closed and
later calls raise `DisconnectedError`.
