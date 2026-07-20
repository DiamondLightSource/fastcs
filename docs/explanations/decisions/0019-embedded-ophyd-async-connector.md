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
  cancels scan tasks and calls `Controller.disconnect()`. Upstream
  `Device.disconnect()` is a follow-up (item 8 in #388 §8), not blocking.
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
| `SignalBackend.get_datakey` | `Attribute.datatype` → `SignalMetadata` (units, precision, limits, choices) + `make_datakey` |
| `CommandBackend.execute`/`.signature` | `Command.__call__` / captured `Signature`, [ADR 15](0015-typed-commands.md) |
| `SignalBackend.source` | e.g. `fastcs://.` |
| child `Device` / `DeviceVector` | sub-`Controller` / `ControllerVector` |
| (not exposed) | `@scan` methods — server-side only, not surfaced to ophyd-async |

Datatype mapping: `Int`/`Float`/`Bool`/`String` → `int`/`float`/`bool`/`str`;
`Waveform(array_dtype, shape)`/`Array1D` hint (per
[ADR 17](0017-naming-pass.md)) → ophyd-async `Array1D[dtype]`; `Enum(cls)` →
the enum class itself. Two mismatches flagged as **prototype risk** in #388
and carried into this ADR unresolved:

- ophyd-async constrains enums to `EnumTypes` (`StrictEnum`/`SubsetEnum`/
  `SupersetEnum`); FastCS accepts any `enum.Enum`.
- fastcs `Table` vs. ophyd-async `Table` (pydantic-based) — mapping is
  best-effort, mismatches should be flagged early rather than silently
  coerced.

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

## Open questions

1. Enum conversion: does the connector require FastCS `Enum` datatypes used
   with embedding to be `StrictEnum`/`SubsetEnum`/`SupersetEnum` subclasses
   (pushing a constraint back onto FastCS driver authors who want embedding
   support), or does it do runtime conversion/wrapping, and what happens to
   values that don't fit ophyd-async's stricter model?
2. `Table` mapping: is a real bidirectional pydantic-model ↔ fastcs-`Table`
   converter in scope for the first cut, or is `Table` explicitly
   unsupported/best-effort-only initially, with a hard error on mismatch
   rather than silent coercion?
3. Where does `@scan`-derived state that isn't exposed as a `Signal` go —
   is it simply invisible to ophyd-async (server-side only, as the mapping
   table states), or does some `@scan` output need a path to surface as a
   `SignalR` (e.g. `fastcs-eiger`'s `update_voltages` `@scan` feeding
   per-ramp `AttrR`s — those `AttrR`s are visible, but would a *pure*
   `@scan`-only value ever need exposing)?
4. How does `embedded_fastcs_connector` handle a `Controller` that raises
   during `initialise()` (e.g. a device that's unreachable at embed time)
   — does `connect_real` propagate the exception directly to
   `Device.connect()`, retry, or something else?
5. Should the embedded connector's shutdown (`atexit` + explicit
   `await connector.shutdown()`) also be triggered by ophyd-async's own
   `Device.disconnect()` once that upstream work lands (#388 §8 item 8), and
   does that imply `ControllerRunner.stop()` needs to be safely callable
   from a synchronous `atexit` context as well as an async one?
