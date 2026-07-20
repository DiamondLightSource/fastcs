# 15. Typed Commands

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

## Status

Proposed

## Context

FastCS `Command` (`src/fastcs/methods/command.py`) is void/void only:
`Method._validate` requires zero parameters and `None`/empty return type.
`UnboundCommand.bind` produces a `Command` wrapping a zero-arg, no-return
async callable. `Method.__init__` already captures the full
`inspect.Signature` of the wrapped function (`method.py:21`), but `Command`'s
own `_validate` throws that signature away by rejecting anything with
parameters.

ophyd-async's equivalent, `Command[P, T]` (`ophyd_async/core/_command.py`),
carries a real parameter and return type, exposed via `CommandBackend.signature`
and `CommandBackend.execute(*args, **kwargs) -> T`. `TriggerableCommand =
Command[[], None]` is the void/void case, expressed as a special case of the
general one rather than the only case.

This gap already shows in our downstream drivers rather than being
speculative: `fastcs-secop` builds command arguments and results dynamically
from SECoP's wire `datainfo` (`_controllers.py:102-110`) — a genuinely typed
(if dynamically-typed) command surface that FastCS's void/void `Command`
cannot represent today, forcing `fastcs-secop` to route command arguments
through attributes on a dedicated `SecopCommandController` instead of a
single typed call.

## Decision

Lift the zero-arg/no-return restriction in `Method`/`Command._validate`, and
introduce `Command[P, T]` generic over parameters and return type, keeping
the already-captured `inspect.Signature` as the public surface —
`ControllerAPI` exposes it directly, mirroring `CommandBackend.signature`.

Transport capability is declared, not assumed uniform:

- **Tango, REST, GraphQL, and the embedded ophyd-async connector** serve
  typed commands fully — arguments and return value round-trip through
  each protocol's native typed-call mechanism.
- **EPICS CA/PVA** stay void/void at the wire level (there is no PV
  representation of "call with these typed arguments, get this typed
  return" that doesn't already exist as separate attributes). They **skip
  typed commands with a warning** at start-up rather than failing to serve
  the controller at all. A command the user explicitly declares as typed
  *and* forces to be served over an EPICS-only transport is a hard error —
  matching the existing ophyd-async connector's behaviour, which errors
  rather than silently drops when a `Device` requires a capability its
  connector cannot provide.

```python
class Ramp(Controller):
    move_to: Command[[float], None]        # typed: not served over CA/PVA
    stop: Command[[], None]                 # void/void: served everywhere
```

## Consequences

- `Command.__call__` gains real `*args`/`**kwargs` forwarding instead of a
  bare `await self.fn()`; `UnboundCommand.bind` needs the same treatment.
- `ControllerAPI` (or its per-command entries) needs to expose the
  signature to transports, so each transport can decide serve-fully /
  serve-with-warning / hard-error per decision above.
- EPICS transports (`transports/epics/ca`, `transports/epics/pva`) need a
  capability check at controller-API-build time, producing a startup-time
  warning log rather than a runtime failure per typed-command call.
- `fastcs-secop`'s dynamically-typed command args/results likely still need
  `Command[Any, Any]` or a per-instance generated type, since SECoP's
  `datainfo` is only known at connect time — full static typing of command
  signatures is not achievable for introspection-driven drivers, only for
  statically-declared ones. This mirrors the same "hint vs. no-hint" tension
  as [ADR 13](0013-declarative-procedural-split-and-controller-filler.md).
- Command args/return values need datatype validation analogous to
  `Attribute`'s `DataType.validate` — whether they reuse the `DataType`
  family directly or a separate mechanism is an open question.

## Open questions

1. Do command arguments/return values validate through the same `DataType`
   family attributes use, or is a separate (lighter-weight, since there's no
   "current value" to cache) validation path introduced?
2. For `fastcs-secop`-style dynamically-typed commands, what's the
   recommended pattern — `Command[Any, Any]` with manual validation inside
   the handler, or a documented way to construct a `Command[P, T]` with `P`/
   `T` determined at runtime (which conflicts with normal generic typing)?
3. Exactly what should the EPICS skip-with-warning message say, and where —
   at controller construction, at `post_initialise`, or lazily the first
   time a typed command is looked up by the transport?
4. Should typed commands support partial typing (e.g. typed arguments but
   void return, or vice versa), or is it all-or-nothing relative to
   `Command[[], None]`?
5. Does the REST/GraphQL/Tango serialisation of complex argument/return
   types (numpy arrays, `Enum`, `Table`) reuse existing `DataType`
   serialisation code from attributes, and if so does that argue for
   sharing more machinery between `Attribute` and `Command` than they do
   today?
