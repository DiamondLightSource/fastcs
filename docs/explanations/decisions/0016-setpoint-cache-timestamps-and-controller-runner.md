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
setpoint, exposed via a public `AttrW.get_setpoint()` method (mirroring
ophyd-async's `SignalBackend.get_setpoint()`), updated whenever `put` is
called, independent of whether the underlying `send` succeeds. This is available to all transports (not just the embedded
connector) as a "what did we last ask for" query distinct from `AttrR.get()`
("what did we last read back").

**Native timestamps (+ severity):** `AttrR.update` accepts an optional
timestamp (and, where meaningful, severity) alongside the value, defaulting
to current time if not supplied by the caller. The timestamp/severity pair
**follows bluesky's `Reading` shape but shares no code** with it, and severity
is a **FastCS enum using the same strings as EPICS** alarm severities. This is
FastCS-native, not EPICS-specific — Tango event pushes and other IO can supply a device-side
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
- The **whole lifecycle including reconnect** — calling `Controller.reconnect()`
  on scan-task failure; reconnect is owned by the runner, not left
  controller-specific.

The runner is a class with `start()`/`stop()` (ophyd-async calls `start`/`stop`;
an `async with` context manager is added only if it also suits the `FastCS()`
case). **Idempotency is the caller's responsibility**, not the runner's — the
embedded connector's `connect_real` may run more than once across reconnects
(see [ADR 19](0019-embedded-ophyd-async-connector.md)).

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

## Resolved in review (#402)

- **Setpoint accessor:** a `AttrW.get_setpoint()` method (mirrors
  `SignalBackend.get_setpoint()`).
- **Timestamp/severity:** follow bluesky's `Reading` shape but **share no
  code**; severity is a **FastCS enum using the same strings as EPICS**.
- **`ControllerRunner`:** a class with `start()`/`stop()` (context manager only
  if it also suits `FastCS()`); **idempotency is the caller's responsibility**.
- **The runner owns the whole lifecycle, including reconnect.**
