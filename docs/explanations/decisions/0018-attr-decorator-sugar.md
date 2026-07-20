# 18. Attr-from-Method Decorator Sugar (@attr_r / @attr_rw)

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

## Status

Proposed

## Context

#388 §7.5 makes the case that FastCS must also sell as a way to write Tango
Device Servers, competing directly with PyTango on the trivial case, not
just on the advanced multi-transport pitch. PyTango's hello-world is one
decorated getter:

```python
@attribute
def current(self) -> float:
    return 2.5
```

Under the [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
harsh declarative/procedural split (bare hints only in the class body; all
IO wiring procedural), the equivalent trivial case regresses to a method
plus a `CallbackReadIO` adapter plus explicit `__init__` wiring — strictly
more ceremony than PyTango for the simple case that most new users hit
first. This is exactly the kind of "false friend" gap #388 warns about: a
PyTango user evaluating FastCS should not find the *simple* case harder than
what they're moving away from.

FastCS already has precedent for binding class-body decorated methods to
per-instance callables without any deepcopy hazard: `@command`/`@scan`
(`src/fastcs/methods/command.py`, `scan.py`) use `UnboundCommand`/
`UnboundScan`, which wrap an unbound function and `.bind(controller)` a
fresh `Command`/`Scan` object per instance at `_bind_attrs` time. Because
these are fresh objects constructed per-instance (not deepcopied
prototypes), they carry none of the aliasing hazard that class-scope
`Attribute` *instances* had — which is why [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
removes the latter but keeps `@command`/`@scan`.

## Decision

Add `@attr_r`/`@attr_rw` (and, for symmetry, whatever `@attr_w`-only case
makes sense) as pure sugar over `AttrR`/`AttrW`/`AttrRW` plus a generated
callback-based `io=`, built on the same `Unbound*`-style bind machinery as
`@command`/`@scan` — fresh objects per instance, no prototype/deepcopy
hazard, consistent with keeping this a class-body citizen under
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md).

```python
class PowerSupply(Controller):
    @attr_rw(units="V", update_period=0.5)   # dtype inferred from -> float
    async def voltage(self) -> float:
        return await self._conn.query("V?")

    @voltage.send
    async def voltage(self, value: float) -> None:
        await self._conn.send(f"V={value}")
```

- The datatype is inferred from the return type annotation of the getter
  (`-> float` → `Float()`), matching how `DataType` mapping already works
  elsewhere (`numpy_to_fastcs_datatype`), rather than requiring a `dtype=`
  keyword the way PyTango does — decision 14 of #388 explicitly calls this
  out as *better* than PyTango's `dtype=` kwarg since it's one real
  annotation, checked statically.
- `@attr_r`/`@attr_rw` decorator keyword arguments (`units`, `update_period`,
  etc.) map onto the equivalent `DataType`/`ReadIO`/`WriteIO` constructor
  arguments from [ADR 14](0014-attribute-io-rw-rework.md) — this is sugar
  over that mechanism, not a parallel one.
- `@attr_rw`'s `.send` decorator mirrors the `@voltage.send` pattern shown
  above (property-style, matching `@property`/`@x.setter`), giving the
  read+write pair a single logical name (`voltage`) with two decorated
  methods.
- This degrades gracefully into the full `io=` object form for protocol
  families with more complex needs, and into
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s
  filler for introspection-driven attributes — `@attr_rw` is explicitly the
  *simple* case, not a replacement for either.
- Refines the class-body rule stated in
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md) to:
  *class body = declarations + decorated behaviour; instance scope =
  construction with data* — already true today via `@command`/`@scan`, now
  extended to attributes.
- Docs gain a "FastCS for PyTango users" page pairing this decorator with
  the equivalent PyTango snippet, landing alongside this PR per #388 §8
  item 5b.

## Consequences

- New driver code for the common "one attribute, one device call" case gets
  noticeably shorter — closing the gap #388 §7.5 identifies against PyTango.
- The generated `io=` object needs a name/shape (an internal
  `CallbackReadIO`/`CallbackWriteIO`-alike, per
  [ADR 14](0014-attribute-io-rw-rework.md)'s open question 5) — this ADR's
  sugar and that ADR's escape hatch should likely share the same underlying
  callback-IO implementation rather than duplicating it.
- Adds a third way to declare an attribute (bare hint + filler; explicit
  `AttrRW(..., io=...)`; `@attr_rw` sugar) — the docs need to be clear about
  when to reach for which, so this doesn't become three equally-weighted
  options with no guidance, undermining the "harsh split" clarity
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md) is
  trying to establish.
- Per #388 §8 item 5b, this sits on PR 1 (the `AttributeIO` rework) —
  small and independent of the `ControllerFiller` work — so it can land
  early and not block on [ADR 13](0013-declarative-procedural-split-and-controller-filler.md).

## Open questions

1. Exact decorator names — `@attr_r`/`@attr_rw` as #388 proposes, or
   something more explicit (`@readable_attribute`?) — and whether a
   write-only `@attr_w` variant is worth adding for symmetry given `AttrW`
   without a paired getter is a rarer shape in practice.
2. How are `min`/`max`/`precision` (post [ADR 17](0017-naming-pass.md))
   and other `DataType`-level metadata passed through the decorator's
   keyword arguments — do they get their own decorator kwargs, or does the
   decorator only take IO-shaped kwargs (`update_period`) and require
   dropping to explicit `AttrRW(...)` construction for richer datatype
   metadata?
3. Does `@attr_rw` support the `Array1D`/`Table` hint spellings from
   [ADR 17](0017-naming-pass.md), or is decorator sugar scoped to scalar
   datatypes only for 1.0?
4. Should `ControllerFiller` treat `@attr_rw`-decorated methods specially
   (they don't need filling — they're already fully constructed at bind
   time), or are they simply invisible to the filler the same way
   `@command`/`@scan` are today?
5. Does the getter's docstring become the attribute's `description`,
   mirroring how `Method._docstring` already captures `getdoc(fn)` for
   `@command`/`@scan`?
