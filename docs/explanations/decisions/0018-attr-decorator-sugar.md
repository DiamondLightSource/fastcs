# 18. Attr-from-Method Decorator Sugar (`@attr` + `@x.setter`)

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388),
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md),
[ADR 14](0014-attribute-io-rw-rework.md)

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
IO wiring procedural), the equivalent trivial case would regress to a method
plus explicit `AttrR(getter=...)` wiring in `__init__` — strictly more
ceremony than PyTango for the simple case that most new users hit first. This
is exactly the kind of "false friend" gap #388 warns about: a PyTango user
evaluating FastCS should not find the *simple* case harder than what they're
moving away from.

FastCS already has precedent for binding class-body decorated methods to
per-instance callables without any deepcopy hazard: `@command`/`@scan`
(`src/fastcs/methods/command.py`, `scan.py`) use `UnboundCommand`/
`UnboundScan`, which wrap an unbound function and `.bind(controller)` a
fresh `Command`/`Scan` object per instance at construction time. Because
these are fresh objects constructed per-instance (not deepcopied
prototypes), they carry none of the aliasing hazard that class-scope
`Attribute` *instances* had — which is why
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
removes the latter but keeps `@command`/`@scan`.

## Decision

Add `@attr` as pure sugar over `AttrR`/`AttrRW` plus generated getter/setter
callables ([ADR 14](0014-attribute-io-rw-rework.md)), built on the same
`Unbound*`-style bind machinery as `@command`/`@scan` — fresh objects per
instance, no prototype/deepcopy hazard, consistent with keeping this a
class-body citizen under
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md). The
decorator mirrors `@property`: `@attr` on the getter, `@voltage.setter` on the
writer.

```python
class PowerSupply(Controller):
    @attr(Polled(0.5), units="V")   # datatype inferred from -> float
    async def voltage(self) -> float:
        """Output voltage."""
        return await self._conn.query("V?")

    @voltage.setter
    async def voltage(self, value: float) -> None:
        await self._conn.send(f"V={value}")
```

- The datatype is inferred from the return type annotation of the getter
  (`-> float` → a `float` attribute), matching how the datatype is inferred
  from a getter's annotation on the procedural
  `AttrR(getter=…)`/`AttrRW(getter=…, setter=…)` form
  ([ADR 14](0014-attribute-io-rw-rework.md)), rather than requiring a `dtype=`
  keyword the way PyTango does — decision 14 of #388 explicitly calls this out
  as *better* than PyTango's `dtype=` kwarg since it's one real annotation,
  checked statically.
- `@attr` comes in two forms: bare `@attr` and parameterised
  `@attr(Polled(0.5), precision=3, units="V")`. The keyword arguments map onto
  the same `*Meta` fields (typed with `Unpack[…Meta]`, validated against the
  getter's return type). The optional leading positional is a **schedule** -
  the same `Polled`/`NotPolled` objects the procedural form wraps its getter in
  ([ADR 14](0014-attribute-io-rw-rework.md), amendment 2026-08-03) - so the two
  spellings share one vocabulary rather than the decorator taking a
  `poll_period=` kwarg the constructor no longer has. Sugar over that
  mechanism, not a parallel one.
- **Bare `@attr` means the same as a bare `getter=`**: read once, when the
  controller connects. This symmetry is why the constructor keeps a default
  instead of demanding a wrapper - a bare decorator has to resolve to some
  schedule, so both sides default to the same safe one:

  | Schedule | Procedural | Declarative |
  |---|---|---|
  | Once, at connect | `AttrR(t, getter=g)` | `@attr(units="V")` |
  | Every 0.5s | `AttrR(t, getter=Polled(g, period=0.5))` | `@attr(Polled(0.5), units="V")` |
  | Never; `poll()` only | `AttrR(t, getter=NotPolled(g))` | `@attr(NotPolled(), units="V")` |
- `@attr`'s `.setter` decorator mirrors `@property`/`@x.setter`, giving the
  read+write pair a single logical name (`voltage`) with two decorated methods.
  There is **no dedicated write-only decorator** — a paired-getter-less `AttrW`
  is rare, so it is written longhand as `AttrW(setter=…)`.
- The getter's docstring becomes the attribute's `description`, as
  `@command`/`@scan` already do.
- `@attr` supports the [ADR 17](0017-naming-pass.md) `Array1D`/`Table` hint
  spellings as the getter's return annotation.
- There is **no** free-function `attr()` factory: the procedural spelling is
  `AttrR(getter=…)` / `AttrRW(getter=…, setter=…)` directly. `@attr` degrades
  gracefully into that procedural form for protocol families with more complex
  needs, and into
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s filler
  for introspection-driven attributes — `@attr` is explicitly the *simple*
  case, not a replacement for either.
- Refines the class-body rule stated in
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md) to:
  *class body = declarations + decorated behaviour; instance scope =
  construction with data* — already true today via `@command`/`@scan`, now
  extended to attributes.
- Docs gain a "FastCS for PyTango users" page pairing this decorator with the
  equivalent PyTango snippet, landing alongside this PR per #388 §8 item 5b.

Interaction with the filler: an `@attr`-decorated attribute is already defined,
so [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s
`ControllerFiller` treats it as filled and does not shadow it; a clash between
an introspected name and a decorated name raises.

## Consequences

- New driver code for the common "one attribute, one device call" case gets
  noticeably shorter — closing the gap #388 §7.5 identifies against PyTango.
- `@attr` and the procedural `AttrR(getter=…)` / `AttrRW(getter=…, setter=…)`
  form are two spellings over one implementation — the generated getter/setter
  from [ADR 14](0014-attribute-io-rw-rework.md), with no separate callback-IO
  classes.
- There are three ways to declare an attribute (bare hint + filler; explicit
  `AttrRW(getter=…, setter=…)`; `@attr` sugar) — the docs need to be clear
  about when to reach for which, so this doesn't become three equally-weighted
  options with no guidance, undermining the "harsh split" clarity
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md) is
  trying to establish.
- Per #388 §8 item 5b, this sits on PR 1 (the per-attribute IO rework) — small
  and independent of the `ControllerFiller` work — so it can land early and not
  block on [ADR 13](0013-declarative-procedural-split-and-controller-filler.md).

## Questions resolved in review (#402)

1. **What is the decorator spelling?** `@attr` + `@x.setter`
   (property-mirroring), not `@attr_r`/`@attr_rw`/`.send`. No dedicated
   write-only decorator — `AttrW` alone is rare, written longhand.
2. **How is datatype/limits metadata passed?** Via decorator kwargs, typed with
   `Unpack[…Meta]` (`precision`, `units`, limits — the
   [ADR 14](0014-attribute-io-rw-rework.md)/[ADR 17](0017-naming-pass.md)
   `*Meta` fields), validated against the getter's return type.
3. **Does it support the `Array1D`/`Table` hints?** Yes, as the getter's return
   annotation.
4. **How does the filler treat a decorated attr?** As already defined — not
   shadowed; a clash between an introspected name and a decorated name raises.
5. **Does the getter's docstring become the `description`?** Yes, as
   `@command`/`@scan` already do.
