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

## Amendment: connections own reconnect (#422)

Question 4 above says the runner owns reconnect, and stops there. It left the
*subject* of reconnect implicit, and the first implementation took it to be the
controller — which cannot work once controllers share a link, because
reconnecting a controller that does not own its connection is a no-op.

The subject is the **connection**. Connection state moves off `Controller` onto a
first-class `Connection` object: controllers hold one, several may hold the same
one, and it owns its own health, reconnect task and retry budget. See
[connections](../connections.md) for the shape, and the design attached to
[issue #422](https://github.com/DiamondLightSource/fastcs/issues/422).

What this amends:

- **`Controller.connect`/`reconnect`/`disconnect`/`_connected` are removed.** A
  driver never touches a connected flag; `Connection.connect()` opens the link or
  raises, and the framework decides what that means.
- **`initialise`/`post_initialise` become `build`/`setup`**, splitting "structure
  that depends on the device" from "hardware writes once the tree is built".
  `build` optionally receives whatever the connection's `connect()` returned.
- **Introspection is checked on every reconnect.** `build` runs once, so a device
  that comes back describing itself differently cannot be accommodated.
- **The runner owns the startup order** — connections, then `build` to a fixpoint,
  then `setup`, then tasks — and shutdown, closing connections in reverse.

Points the review of #420 asked to settle, and how they land:

- **`connection._connected = True` from outside.** There is a framework-only
  `_set_connected()` next to `set_disconnected()`, so the flag has one owner.
- **What "fatal" means for an introspection mismatch.** Not `sys.exit`: an
  embedded FastCS must survive it. The runner sets `fatal_error` (an
  `asyncio.Event`) and records `fatal_reason`; `FastCS.serve` raises it, and an
  embedder observes it instead.
- **How build info is compared.** With `!=`, which means an introspection result
  must compare to a single bool. An ambiguous comparison (a dict of numpy arrays)
  raises a message saying so rather than escaping a background task.
- **`check()` skipped while IO is succeeding.** Dropped. There is no separate
  health-check hook: a connection with any polling is proved alive by that
  polling, and a device that needs a heartbeat gets a `@scan`, which is ordinary
  driver code. A connection nothing polls is warned about at startup.
- **`depends_on` cycles.** Declared rather than derived, and detected at startup.
- **`max_attempts` exhausting to a terminal state.** Kept as the design specifies
  (default 10), and *not* propagated to dependents — the parent's give-up message
  names what it blocks. Whether the default should instead be retry-forever is
  left open; it is one constant.
