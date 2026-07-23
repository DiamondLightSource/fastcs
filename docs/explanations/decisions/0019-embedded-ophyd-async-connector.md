# 19. Embedded ophyd-async Connector

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

## Status

Proposed

## Context

ophyd-async already bridges to FastCS over the network:
`ophyd_async.fastcs.core.fastcs_connector(uri)` is a `PviDeviceConnector`
talking PVA+PVI. #388 proposes an **in-process** embedding as well — running
a FastCS `Controller` directly inside a bluesky/ophyd-async process, with no
network hop, for cases like running a `TemperatureController` straight from
a bluesky plan.

Researching ophyd-async's `DeviceFiller`
(`ophyd_async/core/_device_filler.py`) as the direct structural reference
for [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s
`ControllerFiller` surfaced the exact shape this connector needs to take:

- `DeviceConnector.create_children_from_annotations` builds a `DeviceFiller`
  once (memoised via `hasattr(self, "filler")`), then either fills
  immediately or defers to `connect_real`.
- `connect_real` is where PVI and Tango connectors both actually introspect
  and fill children — there is no ophyd-async precedent for "fill
  everything at construction time" in a connect-time-introspecting
  connector; embedding should follow the same connect-time pattern rather
  than trying to fill eagerly.
- `SignalBackend`'s methods (`get_value`, `get_setpoint`, `set_callback`,
  `put`, `get_datakey`) are the exact surface a `FastCSSignalBackend` needs
  to implement in terms of FastCS's `AttrR.get`/`AttrW.put`/setpoint cache/
  native timestamps (from [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md)).
- `CommandBackend.execute`/`.signature` is the equivalent surface for typed
  commands (from [ADR 15](0015-typed-commands.md)).

This connector is explicitly the motivating consumer for
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md),
[ADR 15](0015-typed-commands.md), and
[ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md) — it is
what forces those three to define a genuinely stable, documented surface
rather than an implicit one, since it lives in a different package
(ophyd-async) and cannot reach into FastCS internals the way FastCS's own
transports currently can.

## Decision

Per decision 6 of #388: no shared package. `FastCSDeviceConnector` lives
entirely on the ophyd-async side, behind an `ophyd-async[fastcs-embed]`
extra, importing only the stable FastCS surface formalised by
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
(`ControllerFiller`) and [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md)
(`ControllerAPI` tree, `ControllerRunner`, and the attribute/command runtime
methods). Convergence is by convention (the two projects agreeing on shape),
not by shared code.

```python
from ophyd_async.fastcs import embedded_fastcs_connector

class TempStage(Device):
    ramp_rate: SignalRW[float]
    power: SignalR[float]
    cancel_all: TriggerableCommand
    ramps: DeviceVector[TempRamp]

stage = TempStage(connector=embedded_fastcs_connector(TemperatureController(settings)))
await stage.connect()          # runs controller lifecycle in-process
```

Mechanics, directly mirroring `PviDeviceConnector`/`TangoDeviceConnector`:

- `create_children_from_annotations`: builds a `DeviceFiller` with
  `FastCSSignalBackend`/`FastCSCommandBackend` factories, `filled=False` —
  same lazy pattern as the network connectors.
- `connect_real` (top level): starts the `ControllerRunner` — `initialise()`,
  `post_initialise()`, `create_api_and_tasks()`, `Controller.connect()`,
  initial coroutines, scan tasks scheduled on the *running* (bluesky) event
  loop — then walks the `ControllerAPI` tree filling children via the
  `DeviceFiller`, `check_filled()`, `set_name()`. Idempotent across
  reconnects, per [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md)'s
  `ControllerRunner` requirement.
- `connect_mock` never touches the controller, so mock-mode ophyd-async
  usage stays free (no FastCS controller/connection is instantiated at all).
- Lifecycle (decision 8 of #388): the connector owns the runner; shutdown
  via an `atexit` hook plus an explicit `await connector.shutdown()`, which
  cancels scan tasks and calls `Controller.disconnect()`. **The
  `Device.disconnect()` proposal is dropped** — reconnect is
  `Device.connect(force_reconnect=True)`, and the only disconnect we want is
  `atexit` (review #402).
- Embedded + transports simultaneously (decision 9 of #388, e.g. a CA GUI
  running next to a bluesky plan) is explicitly out of scope for the first
  cut, but the `ControllerRunner` is designed so a transport list can be
  attached later without redesigning it.

Backend mappings (from #388 §5, grounded against the researched
`DeviceFiller`/`SignalBackend` surface):

| ophyd-async | FastCS |
|---|---|
| `SignalBackend.get_value` | `AttrR.get()` |
| `SignalBackend.set_callback` | `AttrR.add_on_update_callback(cb, always=True)`; stamped per [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md) |
| `SignalBackend.put` | `AttrW.put(value)` |
| `SignalBackend.get_setpoint` | `AttrW` cached setpoint, [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md) |
| `SignalBackend.get_datakey` | `attr.meta` (units, precision, limits) + python-type/enum choices → `SignalMetadata` + `make_datakey` |
| `CommandBackend.execute`/`.signature` | `Command.__call__` / captured `Signature`, [ADR 15](0015-typed-commands.md) |
| `SignalBackend.source` | e.g. `fastcs://.` |
| child `Device` / `DeviceVector` | sub-`Controller` / `ControllerVector` |
| (not exposed) | `@scan` methods — purely-internal periodic coroutines, bound to no `Attr`, not surfaced to ophyd-async (@shihab-dls, #402) |

Datatype mapping: `Int`/`Float`/`Bool`/`String` → `int`/`float`/`bool`/`str`;
`Waveform(array_dtype, shape)`/`Array1D` hint (per
[ADR 17](0017-naming-pass.md)) → ophyd-async `Array1D[dtype]`; `Enum(cls)` →
the enum class itself. Resolved in review (#402):

- **Enums:** un-hinted enum classes introspect at runtime and drop to a string
  datatype retaining the choices as metadata; hint-typed enums require the
  author to duplicate as a `StrictEnum`/`SubsetEnum`/`SupersetEnum` (as they
  would for remote FastCS) for now — revisit once there are use cases.
- **`Table`:** a real bidirectional converter **is in scope for the first
  cut**, used as the opportunity to bring the FastCS and ophyd-async `Table`
  implementations closer together.

## Consequences

- The stable FastCS interface promised in
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)/
  [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md) gets
  its first external, cross-repo consumer — any accidental internal
  dependency this connector picks up is a signal that surface isn't
  actually stable yet.
- This is entirely an ophyd-async-side deliverable (§8 items 6-8);
  fastcs-core work is a dependency, not part of this repo's PRs.
- Per #388 coordination note: items 1-4 of the §8 work plan land in FastCS
  before item 6 is mergeable; prototyping item 6 against a FastCS branch to
  validate the stable interface *before* freezing it is the recommended
  order, i.e. this connector should be prototyped against the `refactor`
  branch here as the other ADRs' implementations land, not written blind
  against a spec.
- `fastcs-demo`'s temperature controller simulation
  (`fastcs.demo.simulation`) is the existing sim device ophyd-async tests
  against — no new simulated device is needed for the first cut.

## Resolved in review (#402)

- **Enums:** un-hinted → runtime-introspect, drop to string keeping choices as
  metadata; hinted → require `StrictEnum`/`SubsetEnum`/`SupersetEnum`
  duplication for now, revisit with use cases.
- **`Table`:** bidirectional converter in scope for the first cut; use it to
  converge the two `Table` implementations.
- **Errors:** FastCS gains a `ConnectionFailedError` (raised when the device
  doesn't respond); the connector converts it to `NotConnectedError` and keeps
  retrying to connect in the background. All other errors surface unconverted.
- **Disconnect dropped:** reconnect is `Device.connect(force_reconnect=True)`;
  the only disconnect is `atexit`. No `Device.disconnect()` proposal (so #388
  §8 item 8 / issue #401 is rewritten accordingly).
- **`@scan` surfaces nothing; `@command` does (@shihab-dls, #402):** confirmed —
  a `@scan`-decorated method is a **purely internal** coroutine run periodically;
  it is *not* bound to an `Attr` and produces **no** `Signal`. All exposed state
  already lives in `Attr` instances: a getter-based `AttrR` schedules its getter
  as a scan-style task *and* is bound to the Attr (→ `Signal`), and a soft `AttrR`
  fed by `@scan` via `update()` is likewise the exposed Signal — so `@scan` needs
  nothing extra surfaced. A `@command` method **is** different: it creates an
  `AttrW` and **is** exposed (the `CommandBackend` row above). Both `@scan` and
  getter/update coroutines are collected onto the running loop in
  `create_api_and_tasks()`; the connector schedules `@scan` coroutines as internal
  tasks but never maps them to Signals.
