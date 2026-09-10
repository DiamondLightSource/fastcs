# The Stable Interface

Most of FastCS is free to change while it is pre-1.0. A narrow part of it is
not: the surface an *embedder* uses — code that runs FastCS controllers inside
another framework rather than serving them over a transport, such as the
ophyd-async connector. That surface is listed here, and an embedder should use
nothing outside it. In particular, nothing should reach into `BaseController`.

## Running controllers: `ControllerRunner`

`ControllerRunner` owns the controller lifecycle and nothing else — no
transports, no interactive shell. `FastCS` is a caller of it.

```python
from fastcs.controllers import ControllerRunner

runner = ControllerRunner(controller, connections=connections)
apis = await runner.build()   # open connections, build the tree, build the APIs
await runner.start()          # setup, run initial tasks, start scanning
...
await runner.stop()           # stop the tasks, close the connections
```

- **`build()`** opens every declared `Connection`, walks the tree calling
  `build()` on each controller, and builds their `ControllerAPI`s. It exists as
  a separate step because anything serving the controllers has to register its
  callbacks *before* the first values are read, or it misses them.
- **`start()`** runs `setup()` across the tree, the initial (`ONCE`) tasks, and
  then starts the periodic ones and one reconnect task per connection. It runs
  `build()` first if you have not, so an embedder that does not need the APIs in
  between can just call `start()`.
- **`stop()`** cancels the tasks and closes every connection, in reverse
  declaration order.

**Idempotency is the caller's responsibility.** Starting a running runner, or
stopping a stopped one, is not defined — an embedder whose own connect may run
more than once has to keep track itself.

The runner also owns **reconnect**, per connection rather than per controller:
see [connections](./connections.md). A connection's own IO marks it down when its
transport fails, and that connection's reconnect task brings it back at its own
pace. This is deliberately not left to each controller, so every connection
recovers the same way and controllers sharing one recover together.

**A fatal runner condition is observable rather than fatal to the process.**
`runner.fatal_error` is an `asyncio.Event` set when the runner cannot carry on —
a device coming back from a reconnect describing itself differently, say — with
`runner.fatal_reason` carrying why. Nothing calls `sys.exit`, so an embedded
FastCS inside another process decides for itself what to do; `FastCS.serve`
raises it.

## Reading the structure: `ControllerAPI`

`ControllerAPI` is the read-only view of a controller:

- `attributes` — the `Attribute`s, by name
- `command_methods` — the `Command`s, by name
- `scan_methods` — the `Scan`s, by name
- `sub_apis` — child `ControllerAPI`s, by name
- `path` and `description`
- `walk_api()` — this API and every descendant

## Reading and writing values: the attribute surface

For an `AttrR` (and so an `AttrRW`):

- `readback` — the last known value
- `timestamp` — when that value was obtained, as a unix timestamp: the time the
  source reported if it reported one, otherwise the time the update arrived
- `severity` — how wrong that value is, as a `Severity`
- `await poll()` — read a fresh value from the getter, cache it, return it
- `await update(value)` — push a value into the cache without any IO. Accepts a
  bare value or an `Update`, which may carry a timestamp and severity
- `add_readback_callback(cb)` — be told when the readback changes

For an `AttrW` (and so an `AttrRW`):

- `setpoint` — the last value asked for. Cached by `set()` *before* the setter
  runs and regardless of whether it succeeds, so it answers "what did we last
  ask for", distinct from `readback`'s "what did we last read"
- `await set(value)` — cache the setpoint and apply it through the setter
- `add_setpoint_callback(cb)` — be told when the setpoint changes

For any attribute: `dtype`, `access_mode`, `description`, `group`, and the
metadata it carries.

## Calling actions: the command surface

- `await command()` — call it
- `command.signature` — what it takes and returns

## Timestamps and severity

A value entering an attribute may carry when it was obtained and how wrong it
is, by arriving as an `Update`:

```python
from fastcs.attributes import Severity, Update

async def get_temperature() -> Update[float]:
    value, device_time = await protocol.read_with_timestamp()
    return Update(readback=value, timestamp=device_time)

async def get_status() -> Update[float]:
    value, fault = await protocol.read_status()
    return Update(
        readback=value,
        severity=Severity.MAJOR if fault else Severity.NO_ALARM,
    )
```

A bare value is stamped with the time it arrived and reported as
`Severity.NO_ALARM`. This matters because a device that already knows when a
value was measured — an EPICS record timestamp, a Tango event — otherwise has
nowhere to say so, and the reading silently becomes "whenever FastCS heard
about it".

`Severity` is a FastCS enum that uses the same strings as EPICS alarm
severities, so a driver or transport speaking EPICS does not have to translate.
It is not EPICS-specific. The value/timestamp/severity trio follows the shape of
bluesky's `Reading` so that the two read the same way, but shares no code with
it.
