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

**Argument and return typing are independent** (not all-or-nothing):

- *Args*: `[]` (none) · `[DT1, DT2, …]` (positional, known types — validated).
- *Returns*: `None` · `DT` (a single typed value).

There is **no partial `Command[Any, Any]`** — a statically-declared `Command`
always has its parameter and return types fully known. The alternative is not
a half-known command but a fully-dynamic controller: a driver that knows
*nothing* statically (`fastcs-secop`, discovering everything from an
over-the-wire `describe`) does not annotate a `Command` at all — it builds the
whole structure, attributes and commands alike, at runtime. So `P`/`T` are
either **completely known** (static declaration) or the **whole structure is
unknown** (runtime construction); there is no in-between case where you know
something is a command but not its signature. This is the same "hint vs.
no-hint" split as [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
for attributes — with no partial hint.

Following the [ADR 17](0017-naming-pass.md) `DataType` drop, command
arguments/returns use plain python types + `*Meta` exactly as attributes do
(no metadata ⇒ "use the python type"), and the serialisation machinery is
**shared** with `Attribute`, not duplicated. **Keyword-argument** commands
need a `TYPE_CHECKING` stub trick and are prototyped separately in the spike
[#403](https://github.com/DiamondLightSource/fastcs/issues/403), not here.

## Consequences

- `Command.__call__` gains real `*args`/`**kwargs` forwarding instead of a
  bare `await self.fn()`; `UnboundCommand.bind` needs the same treatment.
- `ControllerAPI` (or its per-command entries) needs to expose the
  signature to transports, so each transport can decide serve-fully /
  serve-with-warning / hard-error per decision above.
- EPICS transports (`transports/epics/ca`, `transports/epics/pva`) need a
  capability check at controller-API-build time, producing a startup-time
  warning log rather than a runtime failure per typed-command call.
- `fastcs-secop` (and any introspection-driven driver) does **not** get a
  `Command[Any, Any]`. Since SECoP's `describe` reveals the entire structure
  only at connect time — there is no static declaration of *anything*, let
  alone a command of unknown signature — such drivers build their commands
  programmatically at runtime, each carrying a concrete signature derived from
  the wire `datainfo`. Static `Command[P, T]` is for statically-declared
  controllers; fully-dynamic drivers construct commands (or keep the existing
  `SecopCommandController` explode-to-PVs workaround) at runtime instead.
- Command args/return values validate through the **same** python-type +
  `*Meta` mechanism as attributes (the `DataType` family is removed, ADR 17) —
  one shared validation/serialisation path, no command-specific duplicate.

## Resolved in review (#402)

- **Validation shares the attribute path.** With `DataType` dropped (ADR 17),
  command args/returns use python types + `*Meta` like attributes; no separate
  mechanism, and complex-type serialisation (arrays, `Enum`, `Table`) is shared
  with `Attribute`.
- **Args and returns are typed independently** — Args `[]` / `[DT…]`;
  Returns `None` / `DT` (see Decision). Not all-or-nothing, but each is fully
  known — there is no `Any` middle case.
- **No partial `Command[Any, Any]`.** @Tom-Willemsen confirmed on
  [#402](https://github.com/DiamondLightSource/fastcs/pull/402#discussion_r3621453680)
  that SECoP devices are discovered entirely from an over-the-wire `describe`:
  you never statically know something is a command but not its signature — you
  either know the full `Command[P, T]` or you know nothing at all and build the
  whole controller at runtime. `P`/`T` are therefore completely known or the
  whole structure is unknown, with no in-between.
- **EPICS skip-with-warning fires at IOC startup** — post controller
  construction, when the fully populated controllers are handed to the
  transports to serve.
- **Keyword-arg commands → spike [#403](https://github.com/DiamondLightSource/fastcs/issues/403)**
  (interactive/Opus; needs a `TYPE_CHECKING` stub). Out of scope for core
  typed-command work.
