# 16. AttrW Setpoint Cache, Native Timestamps, and ControllerRunner

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388),
[ADR 14](0014-attribute-io-rw-rework.md)

## Status

Proposed

## Context

Three related gaps block a clean embedded ophyd-async connector
(see [ADR 19](0019-embedded-ophyd-async-connector.md)) and are useful to all
transports independently of embedding:

1. **No cached setpoint.** The old `AttrW.put` (`src/fastcs/attributes/attr_w.py`)
   applied a setpoint via `_on_put_callback` but did not retain it anywhere
   queryable. ophyd-async's `SignalBackend.get_setpoint()` — needed for
   `locate()` — has no FastCS equivalent to read from.
2. **No FastCS-native timestamps.** The old `AttrR.update` (`attr_r.py`) stamped
   nothing; individual transports each did their own thing (EPICS records
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

**Setpoint cache.** `AttrW`/`AttrRW` retain the last-applied setpoint, exposed
via the sync `.setpoint` property from [ADR 14](0014-attribute-io-rw-rework.md)'s
runtime surface (the FastCS analogue of ophyd-async's
`SignalBackend.get_setpoint()`). `set(value)` caches it immediately — before
the setter runs and independent of whether the setter succeeds — so `.setpoint`
is a "what did we last ask for" query, distinct from `.readback` ("what did we
last read back"). This is available to all transports, not just the embedded
connector.

**Native timestamps (+ severity).** A value entering an `AttrR`/`AttrRW` may
carry a timestamp and severity by arriving as an `Update[T]`
([ADR 14](0014-attribute-io-rw-rework.md)) — from a getter's return, a
value-returning setter, or a `@scan`/subscription `update()` push — defaulting
to framework receive-time when the timestamp is `None`. The timestamp/severity
pair **follows bluesky's `Reading` shape but shares no code** with it, and
severity is a **FastCS enum using the same strings as EPICS** alarm severities.
This is FastCS-native, not EPICS-specific — Tango event pushes and other IO can
supply a device-side timestamp through the same `Update[T]` path a getter
already uses. The embedded connector stamps receive-time only as an interim
measure until this lands, per decision 10 of #388 — this is 1.0 scope, not a
follow-up.

**ControllerRunner.** Extract the controller lifecycle currently inlined in
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

This, together with `ControllerAPI` and the attribute/command runtime surface
from [ADR 14](0014-attribute-io-rw-rework.md) (`.readback`/`poll()` +
update-callback registration, `set()` + the `.setpoint` cache,
`attr.meta`/`access_mode`/`description`/`group`), becomes the documented stable
surface referenced by decision 13 of #388.

## Consequences

- `FastCS.serve` shrinks to transport orchestration; the controller
  lifecycle it currently inlines becomes independently testable and
  reusable without instantiating a `FastCS` object or any `Transport`.
- Every getter/setter *may* return an `Update[T]` to supply a
  timestamp/severity, but a bare value is unaffected — it defaults to
  framework receive-time, severity unset.
- Transports gain access to a real setpoint distinct from the readback
  value; whether EPICS/Tango/REST/GraphQL surface this as new fields is
  transport-specific follow-up work, not part of this ADR.
- The embedded ophyd-async connector becomes buildable against a documented,
  narrow surface instead of `BaseController` internals — see
  [ADR 19](0019-embedded-ophyd-async-connector.md).

## Questions resolved in review (#402)

1. **How is the cached setpoint exposed?** Via the `.setpoint` property from
   [ADR 14](0014-attribute-io-rw-rework.md)'s runtime surface (the FastCS
   analogue of `SignalBackend.get_setpoint()`), cached by `set()` before the
   setter runs.
2. **What shape do timestamp/severity take?** They follow bluesky's `Reading`
   shape but **share no code**; severity is a **FastCS enum using the same
   strings as EPICS**, carried on `Update[T]`.
3. **What is the runner's shape?** A class with `start()`/`stop()` (context
   manager only if it also suits `FastCS()`); **idempotency is the caller's
   responsibility**.
4. **Who owns reconnect?** The runner owns the whole lifecycle, including
   reconnect.
