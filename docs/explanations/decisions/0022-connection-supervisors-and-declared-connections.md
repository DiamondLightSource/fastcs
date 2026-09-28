# 22. Connections are declared by type hints and owned by a Supervisor

Date: 2026-09-28

## Status

Accepted. Amends [ADR 21](0021-connection-recovery-policy.md).

## Context

Connections had grown several jobs, and several places that each did part of one.

- **The contract lived inside `__init__` bodies.** A controller claimed a connection
  with `connections.get("name", Type)`, possibly several levels down the tree. The
  schema for `fastcs.yaml` could check each connection's settings, but not which names
  were needed or what type each had to be, so the config validated against a shape
  nobody had declared. Lookup by name also broke reuse: a `MotorController` that claims
  `"motor"` cannot be used twice in one tree, for pitch and yaw.
- **Failure was reported twice, by the author.** A connection's IO had to call
  `set_disconnected()` *and* raise. The hook informed the framework and the exception
  informed nobody; a missing hook meant nothing ever reconnected, and nothing
  complained.
- **Health lived on the connection, scheduling on the tree.** The runner built scan
  loops per top-level controller and gated them on the root's connection. A sub
  controller on a different connection kept polling its dead link, and was frozen when
  the root's link dropped even if its own was healthy. A soft root gated nothing.
- **Clients could not see it.** The runner knew a link was down, but clients saw stale
  values with no alarm.
- **Deployment facts and driver facts were mixed.** `depends_on` was written in every
  deployment's YAML, although Odin needs Eiger whatever the deployment, while the retry
  settings were constructor arguments on every connection class.

## Decision

- **Controllers receive connections; they never look them up.** The top-level
  controller declares its connections as constructor arguments hinted with a
  `Connection` type, and passes each down to the children that need it. The launcher
  reads the hints, generates the schema for the entry's `connections:` block - one
  required key per argument, no `type:` - builds each connection from its settings, and
  passes it in. `Connections`, `connections.get` and `launch(connection_classes=...)`
  are removed.
- **A `Supervisor` per connection owns everything ongoing about it:** health, the
  reconnect loop and its retry budget, the scans of the controllers holding it, and
  their `connected` attributes. The `ControllerRunner` owns only the startup and
  shutdown sequence. The launcher creates the supervisors, since their handles must
  exist before the controllers are constructed; an embedder creates them itself.
- **Controllers hold a handle, not the connection.** The handle passes as the
  connection's type for type checkers and `isinstance`, and routes every `async` method
  call through the supervisor's boundary, which fails fast with `DisconnectedError`
  while the link is down, turns a link failure into `DisconnectedError` and marks the
  link down, and passes a device error back unchanged. `set_disconnected()`,
  `connected`, `wait_up()` and `wait_down()` are removed from `Connection`, which is
  left with a constructor, `connect`, `close`, its IO methods, `depends_on` and
  `policy`.
- **`ConnectionPolicy` classifies failures.** `Recovery` becomes `ConnectionPolicy`, a
  frozen set of independent settings: which errors mean disconnection (by default
  `OSError`, `EOFError` and httpx transport errors), which are timeouts and how many in
  a row mean disconnection (3), which reconnect failures are terminal, and whether that
  is fatal. `DRANode` becomes `DRAPolicy`.
- **Dependencies are declared by type, on the Connection class:**
  `OdinConnection.depends_on = [EigerConnection]` means wait for every
  `EigerConnection` under the same top-level controller. Connections open in that order
  at startup; the rules are resolved into a fixed graph, checked for cycles, at the
  seal.
- **Retry settings are framework keys in `fastcs.yaml`,** `reconnect_attempts` and
  `reconnect_period` on each connection, read by the supervisor.
- **Scans are owned per connection.** Each attribute and `@scan` method belongs to the
  supervisor of its controller's connection, grouped by period, and is paused while
  that link is down. A soft controller's work runs in a loop owned by the runner.
- **Health is published as attributes.** Every controller gets a read-only
  `connected` attribute at the seal, kept in step by its supervisor and always on for a
  soft controller. When a link drops, every value its supervisor polls is republished
  with `Severity.INVALID`, and a readback callback now fires when only the severity
  changes. No transport changes.
- **Read-once reads re-run after every reconnect; `setup()` does not.** Configuration a
  device needs every time it comes back belongs in `connect()`.
- **An explicit seal.** After the build phase, `connected` is added and the tree is
  frozen: adding an attribute, method or sub controller afterwards raises, rather than
  silently never reaching a transport.
- **A put on a dead link fails back to the caller** instead of being logged and
  swallowed.

## Consequences

`fastcs.yaml` is fully schema-checked, and a controller class can be reused for any
number of instances. Driver IO needs no error handling for the link: nothing is left
to forget, and a getter that swallows an exception cannot hide a dead link, because the
supervisor saw it first. Each connection pauses and recovers on its own, which fixes
the root-gating bug, and clients see a dead link as `connected` off and invalid values.

Every controller's API gains a `connected` attribute, so every transport serves one
more PV or field per controller.

Deferred to later work, as the design proposed:

- Introspection on reconnect - comparing what the device reports about itself with
  what it reported at startup.
- Connections created during `build`, handed to the framework with an explicit `open()`.
  Until then, a controller holding a connection no supervisor owns is rejected at the
  seal.
- Push-based connections, whose failures never pass the boundary.
- Selecting a simulated connection from the YAML, and choosing a policy per instance
  there.
- Several connections of one type in one entry wait on all of them; an explicit
  per-instance override is not provided.
