# Connections

A `Connection` is a link to hardware. It is small: a constructor, `connect`, `close`
and the IO methods a driver needs, plus an optional list of the connection types it
depends on. It holds no health state and has no hooks.

Everything ongoing about the link belongs to its `Supervisor`: whether it is up,
reconnecting it and its retry budget, the scans of the controllers that use it, and
their `connected` attributes. There is one supervisor per connection, so connections,
not controllers, are the unit of failure and recovery. A tree of five sub controllers
behind one socket has one health state, one reconnect loop and one retry budget
between them.

## Writing one

Subclass `Connection`. Its constructor arguments are its device settings - they become
its block in `fastcs.yaml` - and its IO methods just talk to the device:

```python
from fastcs.connections import Connection


class DetectorConnection(Connection):
    def __init__(self, host: str, port: int = 8000) -> None:
        self._base = f"http://{host}:{port}"
        self._client: AsyncClient | None = None

    async def connect(self) -> None:
        self._client = AsyncClient(base_url=self._base)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str):
        response = await self._client.get(path)
        response.raise_for_status()
        return response.json()["value"]
```

There is no `try`/`except` in `get`. When something goes wrong, IO methods just raise,
and the supervisor decides what the exception means - see the boundary, below.

`connect` means "make the link usable", not merely "open the socket": a device that
needs a mode set every time it comes back has that write here. `close` must tolerate
being called on a link that is already closed - the supervisor calls it before every
reconnect attempt.

### The ones that come with FastCS

Most drivers need none of the above. `IPConnection` and `SerialConnection` cover the
stream transports; `HTTPConnection` covers REST devices, with `get`, `get_bytes`,
`put` and the `request` underneath them, so a driver that only needs a different URL
layout writes that and nothing else:

```python
class DetectorConnection(HTTPConnection):
    # The detector wraps every value as {"value": ...}, which is the detector's
    # convention rather than HTTP's - so this is the whole subclass.
    async def get(self, path: str):
        return (await super().get(path))["value"]
```

`SimConnection` is the base for a simulated device: it opens and closes trivially and
can never fail. A simulator is a *sibling* of the real transport rather than a
subclass - inheriting `SerialConnection` would inherit a serial handle it never opens.

## Holding one

A controller is given its connection; it never looks one up. The top-level controller
declares its connections as type-hinted constructor arguments, and passes each down to
the children that need it:

```python
class DetectorController(Controller):
    # Narrows the base class's connection so this controller's own code can call
    # the methods of the connection it actually holds.
    connection: DetectorConnection

    def __init__(self, connection: DetectorConnection) -> None:
        self.connection = connection
        super().__init__()

    async def build(self) -> None:
        # Every connection is open by now, so this can ask the device what it has.
        for parameter in await self.connection.get("detector/api/1.8.0/config/keys"):
            ...  # one attribute per reported key


class EigerController(Controller):
    # The class named in fastcs.yaml. Its type hints declare the connections.
    def __init__(self, eiger: DetectorConnection, odin: OdinConnection) -> None:
        super().__init__()
        self.EIGER_DET = DetectorController(eiger)
        self.ODIN = OdinController(odin)
```

Because the parent passes connections in, the same class works for any number of
instances - a `MotorController` can be used for pitch and yaw in one tree - and naming
stays at the top level, where the schema can check it.

What arrives in the constructor is not the connection itself but a **handle**: a
stand-in that passes as the declared type, for type checkers and `isinstance` alike,
and routes every `async` method call through the connection's supervisor. Authors
never create or see a handle type. Two handles to one connection need not be the same
object, so the framework compares the connections behind them.

A controller's attributes talk only to its own connection - two devices means two
controllers. That is what lets each attribute belong to exactly one supervisor. A
controller that needs another device's behaviour calls a method on the controller
that owns it. A controller with no connection at all (a soft controller that only
groups others, or a `ControllerVector`) is never paused.

Tests need none of this: a controller only needs something that behaves like its
connection type, so a test passes the connection itself, or a mock.

## Configuring them

`fastcs.yaml` names each connection under the controller's entry, by its constructor
argument name. The schema is generated from the type hints, so it knows exactly which
names are required and what settings each has:

```yaml
controllers:
  - id: EIGER
    type: mydriver.EigerController
    connections:
      eiger:
        settings: {host: eiger.local}
      odin:
        settings: {host: odin.local, port: 8888}
        reconnect_attempts: 5
        reconnect_period: 2.0
```

There is no `type:` - the type hint supplies it - and no `depends_on`, which is
declared on the Connection class. `reconnect_attempts` and `reconnect_period` are
framework settings, read by the supervisor: retry behaviour is a fact about a
deployment, not about a device class.

An application that builds its own tree does what the launcher does - create each
connection, give it a supervisor, and pass the handle to the controller:

```python
eiger = Supervisor(DetectorConnection("eiger.local"), name="eiger")
controller = EigerDetController(eiger.handle)
FastCS(controller, transports, supervisors=[eiger]).run()
```

See [](../how-to/launch-framework.md) for the configuration in full.

## Startup

The `ControllerRunner` owns the startup and shutdown sequence, and nothing ongoing:

1. Open every connection, in dependency order. Any failure exits.
2. Walk the tree calling `build`, repeating over anything newly added until a pass
   adds nothing.
3. **Seal** the tree: resolve dependencies into a fixed graph, hand each controller's
   scans to its connection's supervisor, add the `connected` attributes, and freeze
   the API. Nothing can be added to the tree after this.
4. Transports attach, before any values flow.
5. Call `setup` once across the whole tree, run the read-once reads, and hand over to
   the supervisors.

A connection that fails at startup stops the application, and the orchestrator
restarts it. Restarting at startup is cheap, and a partly built tree - clients connect
successfully and never find what they are looking for - is worse than none.

## The boundary

Every call through a handle passes its supervisor's boundary, which:

- fails fast with `DisconnectedError`, doing no IO, if the link is already down,
- turns a link failure into `DisconnectedError`, marks the link down and wakes the
  reconnect loop,
- passes a device error - the device said no - back to the caller unchanged.

Someone has to tell "the link is gone" from "the device said no", and the boundary
can do it broadly because it only ever sees exceptions from connection IO. The
connection's `ConnectionPolicy` says which is which. By default:

| What happened                                       | Treated as                    |
| --------------------------------------------------- | ----------------------------- |
| Connection reset, broken pipe, refused, unreachable | Disconnected                  |
| EOF or an incomplete read                           | Disconnected                  |
| Device node vanished (DRA)                          | Disconnected                  |
| Timeouts                                            | Disconnected after 3 in a row |
| Error reply, rejected value, HTTP error status      | Passed to caller              |
| Parse errors                                        | Passed to caller              |

A device with its own way of saying "I'm offline", such as a special reply, may raise
`DisconnectedError` directly; that is optional.

Because the supervisor sees the error where it starts, a getter with a broad
`try`/`except` cannot hide a dead link, and every caller is covered - getters,
setters, commands and spawned tasks alike. A put on a dead link fails back to the
client rather than being logged and swallowed. Calls a connection makes to its own
methods do not pass the handle, so each call from a controller is counted once.

Detection is passive: a connection is only known to be down once something uses it. A
supervisor with no polling among its controllers warns about that at startup.

## While it is up, and while it is not

Each attribute and `@scan` method belongs to the supervisor of its controller's
connection, grouped by period, one task per period. A soft controller's periodic work
runs in a loop owned by the runner. So each connection is paused on its own: a
struggling Odin link cannot delay Eiger polls that share its period, and a child on a
healthy link keeps polling when its parent's link drops.

When a link goes down, its supervisor pauses its scans, turns the `connected`
attribute of each controller holding it off, and marks every polled value it owns as
`Severity.INVALID`, so clients see stale values flagged rather than stale values that
look fine. Scans stay silent about `DisconnectedError`: the supervisor has already
said so. Other scan errors are logged once, then as a periodic reminder, until the
scan succeeds again.

When it comes back, `connected` goes on, the read-once reads of the controllers
holding it run again - a rebooted device can come back with different values - and the
scans resume. `setup` does not run again: configuration a device needs every time it
comes back belongs in `connect`.

Every controller has a read-only `connected` attribute, added at the seal. A soft
controller reports it as always on. It is an ordinary attribute, so every transport
publishes it with no changes.

## Reconnecting

```
forever:
    sleep until the link is marked down

    loop:
        wait until every connection it depends on is up  # attempts are not used
        if one of them gave up:
            stall and stop                               # only a restart can help

        close(), then connect()

        if it worked:
            mark link up    # connected on, read-once reads again, scans resume
            back to sleeping

        attempts += 1
        if attempts == reconnect_attempts:
            give up and stop    # connected stays off; dependents stall
        sleep reconnect_period
```

The supervisor logs each transition once: down, retrying, back up, giving up.

### Policies

Some failures are known never to recover, and retrying them is noise. The
`ConnectionPolicy` says which: a failed reconnect the policy calls terminal gives up
at once instead of burning the rest of the budget, and the "Giving up" log line says
why. A device node injected by a Kubernetes DRA claim is the case that motivated it -
the claim is made when the pod starts, so a node that has gone will not come back
without a restart:

```python
from fastcs.connections import DRAPolicy, SerialConnection


class StageConnection(SerialConnection):
    policy = DRAPolicy()
```

`DRAPolicy` is also *fatal*: rather than stall with its dependents serving stale
values, it asks the runner to shut the application down, so the orchestrator restarts
the pod and the claim is re-established.

A policy is a set of independent settings rather than one class per behaviour - which
errors count as disconnection, the timeout count, which failures are terminal, and
whether that is fatal - so a slow DRA device is `DRAPolicy(timeout_count=10)`, not a
new class. It is a class attribute of the connection rather than a constructor
argument, so it does not appear in the connection's configuration, and a `Supervisor`
can be given a different one.

### Dependencies

Dependencies are declared by type, on the Connection class - a driver fact, written
once by whoever knows it:

```python
class OdinConnection(HTTPConnection):
    depends_on = [EigerConnection]
```

This reads as: wait for every connection of those types under the same top-level
controller. At startup, connections open in dependency order, so every Eiger before
any Odin. At the seal the type rules are resolved into a fixed graph between
instances, and checked for cycles; reconnect loops use that graph.

All of them must be up: a connection layered over two links is no more usable with
one of them than with neither. While any is down, the dependent waits instead of
attempting, so its retry budget is not burnt against a dead dependency. If any one of
them gives up entirely, the dependent stalls, saying so, rather than retrying
something that cannot work. Scoped to one top-level controller there is normally one
connection of each type; where there are several, a dependent waits for all of them.

## Shutdown

Closing is a runner operation, not an author hook: every connection is closed in
reverse of the order it was opened, so anything layered over another is closed before
what it rides on. `setup` is not undone - devices keep their last configured state.
