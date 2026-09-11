# Connections

A `Connection` is a link to hardware, and it owns its own health state. Controllers
hold a connection; several controllers may hold the same one.

Connections, not controllers, are the unit of failure and recovery. A tree of five
sub controllers behind one socket has one health state, one reconnect task and one
retry budget between them - not five of each, four of which can do nothing about the
link that is actually down.

## Writing one

Subclass `Connection`, open the link in `connect` and close it in `close`:

```python
from fastcs.connections import Connection


class DetectorConnection(Connection):
    # Class defaults sit between the framework defaults and any constructor argument.
    reconnect_period = 5.0
    reconnect_attempts = 60

    def __init__(self, settings: DetectorSettings, **kwargs) -> None:
        super().__init__(**kwargs)
        self._settings = settings
        self._client: AsyncClient | None = None

    async def connect(self) -> None:
        base = f"http://{self._settings.host}:{self._settings.port}"
        self._client = AsyncClient(base_url=base)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str):
        try:
            response = await self._client.get(path)
        except (ConnectError, ReadTimeout):
            # The transport is gone. Everything holding this connection is now down.
            self.set_disconnected()
            raise
        # A 400 from the detector is a device complaint, not a dead link - it
        # propagates to the caller without touching connection state.
        response.raise_for_status()
        return response.json()["value"]
```

`connect` means "make the link usable", not merely "open the socket": a device that
needs a mode set before a driver can read it has that write here.

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

`SimConnection` is the base for a simulated device: it opens and closes trivially,
can never fail, and its reconnect task idles forever. A simulator is a *sibling* of
the real transport rather than a subclass - inheriting `SerialConnection` would
inherit a serial handle it never opens - and which one an application gets is decided
by `type:` in `fastcs.yaml`, not by a magic port value or an environment check.

**The important part is the `except` clause.** The connection is the only place that
can tell "the socket died" from "the device rejected that parameter", and only the
first is a connection failure. Nothing above a connection has to catch anything, and
no exception type is a contract between layers.

**The framework sets the state; authors do the work and raise.** No driver touches a
connected flag: `connect` opens the link or raises, and the framework decides what
that means. The one thing a driver calls is `set_disconnected`, from its own IO.

## Holding one

A controller claims a connection by name from the `Connections` registry, which is
forwarded down the tree. Passing the registry rather than a bare connection means a
controller's constructor signature does not change when something three tiers below
it needs a new connection:

```python
class DetectorController(Controller):
    # Narrows the base class's connection so this controller's own code can call
    # the methods of the connection it actually holds.
    connection: DetectorConnection

    def __init__(self, connections: Connections) -> None:
        # Claimed by name, type asserted. Raises at construction - before anything
        # opens - if the name is missing or the type is wrong.
        self.connection = connections.get("detector", DetectorConnection)
        super().__init__()

    async def build(self) -> None:
        # The connection is open by now, so this can ask the device what it has.
        for parameter in await self.connection.get("detector/api/1.8.0/config/keys"):
            ...  # one attribute per reported key
```

A controller holds at most one connection - two devices means two controllers. A
controller with no connection at all (a soft controller that only groups others, or a
`ControllerVector`) is never gated and never reconnected.

**No controller ever reads another controller's state.** A sub controller that shares
its parent's connection is not consulting its parent - it holds the same object.
Failure, gating and recovery all resolve through that shared object, never through
the tree.

## Declaring them

Every connection an application has is declared up front, which is what lets the
runner open them all before the tree is walked. The registry is built once and
forwarded down the tree - by hand, or by the launcher from the `connections:` block
of a controller's own entry in `fastcs.yaml`:

```yaml
controllers:
  - id: PITCH
    type: fastcs_motor.MotorController
    connections:
      motor:
        type: fastcs.SerialConnection
        settings: {port: /dev/ttyS0}
```

Per entry rather than globally, because the key is the *role* the driver asks for and
the entry identifies the instance: two motors both claim `"motor"` and each resolves
to a different object. A single global block cannot express that, since the driver's
hardcoded role name and the deployment's instance name would have to be the same
string.

A connection cannot be created later: one made during `build` could not have been
opened before the tree was walked, so it would never be supervised or reconnected,
and the runner rejects it saying so.

The consequence of the per-entry block is that sibling entries cannot share a
connection or depend on each other. A gateway with several instruments behind one link is one tree, with the
gateway as the top-level controller.

See [](../how-to/launch-framework.md) for the configuration in full.

## Startup

The `ControllerRunner` owns the order:

1. Open every connection, in dependency order - declaration order, except that
   anything named in a `depends_on` is opened before whatever names it.
2. Walk the tree calling `build`, repeating over anything newly added until a pass
   adds nothing.
3. Call `setup` across the whole built tree.
4. Warn about anything suspicious, run the initial reads, and start the tasks.

A failure anywhere in startup aborts. A partly built tree means an application with a
silently incomplete set of parameters, which is worse than no application at all,
because clients connect successfully and never find what they are looking for. The
orchestrator owns the retry.

## Failure and recovery

Failure is detected in exactly one place: the connection's own IO. `set_disconnected`
wakes that connection's reconnect task and gates every scan that uses it.

There is one reconnect task per connection, idle until that connection actually goes
down - a healthy connection costs nothing, and each connection recovers at its own
pace. A detector that wants to retry every five seconds does not have to compromise
with a writer that wants one.

Each attempt closes the link and reopens it. `reconnect_attempts` consecutive
failures is terminal until the process restarts; a clean connection restores the
budget.

Some failures are known never to recover, and retrying them is noise. A connection
holds a `Recovery` policy that says which: a failed reconnect the policy calls
terminal gives up at once instead of burning the rest of the budget, and the
"Giving up" log line says why. The default policy calls nothing terminal. A device
node injected by a Kubernetes DRA claim is the case that motivated it - the claim is
made when the pod starts, so a node that has gone will not come back without a
restart:

```python
from fastcs.connections import DRANode, SerialConnection


class StageConnection(SerialConnection):
    recovery = DRANode()
```

`DRANode` is also *fatal*: rather than stall with its dependents serving stale
values, it asks the runner to shut the application down, so the orchestrator
restarts the pod and the claim is re-established. A policy that is terminal but not
fatal gives up and stalls, like a spent budget.

The policy is held rather than inherited, so the transport and what to do when it
fails are chosen separately. The same `DRANode()` serves a serial port, a socket or
anything else, with no class per transport × policy, and it can be assigned to a
single instance (`connection.recovery = DRANode()`) as well as set on a class. It
is not a constructor argument, so it does not appear in the connection's
configuration. A policy names the device it gave up on by the connection's `label` -
the port, address or URL where the connection knows one.

`reconnect_period` and `reconnect_attempts` stay on the connection rather than the
policy: whether a failure is terminal is a fact about the device, while the period
and the budget are what a site tunes.

### Dependencies

A connection layered over others declares them, rather than having them derived from
where controllers sit in the tree - one, or several:

```python
odin = OdinConnection(settings, depends_on=detector)
motion = PmacMotionConnection(settings, depends_on=[ssh, status])
```

All of them must be up: a connection layered over two links is no more usable with
one of them than with neither. While any is down, the dependent waits instead of
attempting - and because no attempt means no increment, its retry budget freezes
rather than being burnt against a dead dependency. If any one of them gives up
entirely, the dependent is released rather than left hanging: it logs that it is
stalled and waits for a restart. Cycles are caught at startup, and in config before
that.

## Warnings

- A connection declared but never claimed is warned about at startup: it would
  otherwise be opened and reconnected forever while doing nothing.
- A connection with no polled attribute or scan method among any of its controllers
  is warned about, phrased as fact rather than fault - all-on-demand is a legitimate
  design, it just means nothing will detect the link failing until the next write.

There is no separate health-check hook. A connection with any polling is proved alive
by that polling; a device that genuinely needs a heartbeat gets a `@scan` on one of
its controllers, which is ordinary driver code.

## Shutdown

Closing is a runner operation, not an author hook: every connection is closed in
reverse declaration order, so anything layered over another is closed before what it
rides on. `setup` is not undone - devices keep their last configured state.
