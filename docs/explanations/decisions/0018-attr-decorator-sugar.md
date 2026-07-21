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

> **Review update (#402): the decorator is `@attr`, mirroring `@property`.**
> `@attr` on the getter, `@voltage.setter` on the writer (not `@attr_r`/
> `@attr_rw`/`.send`). It unifies with the callback IO from
> [ADR 14](0014-attribute-io-rw-rework.md): `self.x = attr(getter=…, setter=…)`
> is the `__init__` spelling of the same thing. `AttrW`-only (write with no
> paired getter) is rare, so it is written longhand rather than given its own
> decorator.

Add `@attr` as pure sugar over `AttrR`/`AttrRW` plus a generated
callback-based `io=`, built on the same `Unbound*`-style bind machinery as
`@command`/`@scan` — fresh objects per instance, no prototype/deepcopy
hazard, consistent with keeping this a class-body citizen under
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md).

```python
class PowerSupply(Controller):
    @attr(units="V", update_period=0.5)   # dtype inferred from -> float
    async def voltage(self) -> float:
        return await self._conn.query("V?")

    @voltage.setter
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
- `@attr`'s `.setter` decorator mirrors `@property`/`@x.setter`, giving the
  read+write pair a single logical name (`voltage`) with two decorated
  methods. (`.send` is not used.)
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
- The generated callback `io=` **is** the unified callback mechanism from
  [ADR 14](0014-attribute-io-rw-rework.md) (which no longer ships separate
  `CallbackReadIO`/`CallbackWriteIO`): `@attr` and `attr(getter=…)` are two
  spellings over one implementation.
- Adds a third way to declare an attribute (bare hint + filler; explicit
  `AttrRW(..., io=...)`; `@attr_rw` sugar) — the docs need to be clear about
  when to reach for which, so this doesn't become three equally-weighted
  options with no guidance, undermining the "harsh split" clarity
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md) is
  trying to establish.
- Per #388 §8 item 5b, this sits on PR 1 (the `AttributeIO` rework) —
  small and independent of the `ControllerFiller` work — so it can land
  early and not block on [ADR 13](0013-declarative-procedural-split-and-controller-filler.md).

## Resolved in review (#402)

1. **Decorator is `@attr` + `@x.setter`** (property-mirroring), not
   `@attr_r`/`@attr_rw`/`.send`. No dedicated write-only decorator — `AttrW`
   alone is rare, written longhand.
2. **Datatype/limits metadata passes via decorator kwargs** (`precision`,
   `units`, limits — the ADR 17 `*Meta` fields).
3. **Supports the ADR 17 `Array1D`/`Table` hints.**
4. **`@attr`-decorated attrs are treated specially by the filler** — already
   defined, so not shadowed; a clash between an introspected name and a
   decorated name raises.
5. **Yes — the getter's docstring becomes the attribute's `description`**
   (as `@command`/`@scan` already do).
