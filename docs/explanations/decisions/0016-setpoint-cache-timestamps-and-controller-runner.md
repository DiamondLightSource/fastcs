# 16. AttrW Setpoint Cache, Native Timestamps, and ControllerRunner

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

## Status

Proposed

## Context

Three related gaps block a clean embedded ophyd-async connector
(see [ADR 19](0019-embedded-ophyd-async-connector.md)) and are useful to all
transports independently of embedding:

1. **No cached setpoint.** `AttrW.put` (`src/fastcs/attributes/attr_w.py`)
   applies a setpoint via `_on_put_callback` but does not retain it anywhere
   queryable. ophyd-async's `SignalBackend.get_setpoint()` — needed for
   `locate()` — has no FastCS equivalent to read from.
2. **No FastCS-native timestamps.** `AttrR.update` (`attr_r.py`) stamps
   nothing; individual transports each do their own thing (EPICS records
   get a timestamp from the record subsystem, Tango pushes are unstamped).
   An embedded connector currently has no choice but to stamp receive-time
   only, which is a real information loss versus what the underlying device
   protocol may already provide (Tango event timestamps, EPICS record
   timestamps at source).
3. **No documented, extracted runtime.** `FastCS.serve` (`control_system.py`)
   inlines the full controller lifecycle — `initialise()` →
   `post_initialise()` → `create_api_and_tasks()` → `connect()` → initial
   coroutines → scan tasks — as private logic inside the `serve` coroutine.
   There is no standalone object an embedding connector can start/stop
   without also pulling in `FastCS`'s transport-serving and interactive-shell
   concerns.

Decision 13 of #388 requires this lifecycle, plus `ControllerAPI` and the
attribute/command runtime methods, to be formalised as fastcs-core's single
documented "stable interface" that the ophyd-async connector is restricted
to using — no reaching into `BaseController` internals.

## Decision

**Setpoint cache:** `AttrW` gains an internally-tracked last-applied
setpoint, exposed via a public getter (name TBD — see open questions),
updated whenever `put` is called, independent of whether the underlying
`send` succeeds. This is available to all transports (not just the embedded
connector) as a "what did we last ask for" query distinct from `AttrR.get()`
("what did we last read back").

**Native timestamps (+ severity):** `AttrR.update` accepts an optional
timestamp (and, where meaningful, severity) alongside the value, defaulting
to current time if not supplied by the caller. This is FastCS-native, not
EPICS-specific — Tango event pushes and other IO can supply a device-side
timestamp through the same path a `ReadIO.update` call already uses. The
embedded connector stamps receive-time only as an interim measure until this
lands, per decision 10 of #388 — this is 1.0 scope, not a follow-up.

**ControllerRunner:** Extract the controller lifecycle currently inlined in
`FastCS.serve` into a standalone `ControllerRunner` (or equivalent
`Controller.serve()`/`Controller.stop()` API), independent of the
transport-serving and interactive-shell logic that stays in `FastCS`/
`control_system.py`. `FastCS.serve` becomes a thin caller of
`ControllerRunner` plus transport wiring. The runner owns:

- Running `initialise()`/`post_initialise()`/`create_api_and_tasks()` once.
- Running `connect()` and the initial coroutines.
- Starting/stopping the periodic scan tasks.
- Being **idempotent** — safe to call start again after a stop, since the
  embedded connector's `connect_real` may run more than once across
  reconnects (see [ADR 19](0019-embedded-ophyd-async-connector.md)).

This, together with `ControllerAPI` and the attribute/command runtime
methods (`AttrR.get`/`add_on_update_callback`, `AttrW.put` + cached
setpoint, `Attribute.datatype`/`access_mode`/`description`/`group`), becomes
the documented stable surface referenced by decision 13 of #388.

## Consequences

- `FastCS.serve` shrinks to transport orchestration; the controller
  lifecycle it currently inlines becomes independently testable and
  reusable without instantiating a `FastCS` object or any `Transport`.
- Every `ReadIO.update` implementation *may* supply a timestamp/severity,
  but existing IO that does not is unaffected — defaults to current time,
  severity unset.
- Transports gain access to a real setpoint distinct from the readback
  value; whether EPICS/Tango/REST/GraphQL surface this as new fields is
  transport-specific follow-up work, not part of this ADR.
- The embedded ophyd-async connector becomes buildable against a documented,
  narrow surface instead of `BaseController` internals — see
  [ADR 19](0019-embedded-ophyd-async-connector.md).

## Open questions

1. Setpoint cache accessor name and shape — `AttrW.setpoint` property,
   `AttrW.get_setpoint()` method (mirroring `SignalBackend.get_setpoint()`),
   or folded into `AttrW.put`'s return value?
2. Timestamp/severity type — reuse a existing convention (e.g.
   `ophyd_async`/bluesky's `Reading`/event-model shape) or define a
   FastCS-native pair? Decision 12 of #388 already aligns numeric limits
   naming with event-model `Limits` — should timestamps/severity follow the
   same alignment for consistency?
3. Severity: what are the FastCS-native severity levels, and do they map
   1:1 to EPICS alarm severities, or is EPICS's severity model transport-
   specific with FastCS defining its own smaller/different vocabulary?
4. Exact `ControllerRunner` API shape — a class with `start()`/`stop()`, or
   `async` context-manager semantics (`async with runner:`)? The embedded
   connector needs idempotent start across reconnects; does the chosen shape
   make idempotency the caller's responsibility or the runner's?
5. Does `ControllerRunner` own reconnect logic (calling `Controller.reconnect()`
   on scan-task failure, as `Controller._create_periodic_scan_coro` does
   today), or does that stay controller-specific and out of the runner's
   documented surface?
