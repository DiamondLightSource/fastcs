# 17. Naming Pass: precision, Limits Alignment, Array1D/Table Hints

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388),
[ADR 14](0014-attribute-io-rw-rework.md), [ADR 15](0015-typed-commands.md)

## Status

Proposed

## Context

FastCS and ophyd-async independently arrived at similar concepts with
different names, which is exactly the "false friend" risk #388 opens with:
a developer moving between the two projects can be misled into assuming a
name means the same thing, or reach for a name that does not exist.

Concretely, `_Numeric` (`src/fastcs/datatypes/_numeric.py`) and `Float`
(`src/fastcs/datatypes/float.py`) use `prec`/`min`/`max`/`min_alarm`/
`max_alarm`; ophyd-async and the wider bluesky event-model use
`precision` and a `Limits` structure (`Limits(low, high)` per category, e.g.
control/display/alarm/warning) rather than five flat fields. FastCS's
`Waveform(array_dtype, shape)` and `Table` datatypes have no hint-level
spelling analogous to ophyd-async's `Array1D[np.int32]` (a `numpy.ndarray`
subscripted for shape) and `Table` (pydantic-based) hint syntax — under
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md), bare
hints are now load-bearing (they are what `ControllerFiller` scans), so
having ophyd-async-compatible hint spellings for array/table attributes
becomes more valuable than it was when hints were validation-only.

This pass now lives on python types + `*Meta` typed dicts, not on `DataType`
classes: the `DataType` family is dropped ([ADR 14](0014-attribute-io-rw-rework.md),
[ADR 15](0015-typed-commands.md)), so these renames fold into the per-attribute
IO rework (issue #392) rather than landing as a separate late PR. The concrete
`*Meta` mechanism (per-datatype `TypedDict`s, the superset `Meta` for extras,
`attr.meta` storage, the `Unpack` overloads) is specified in
[ADR 14](0014-attribute-io-rw-rework.md); the module home for these public
names is decided in #406.

Since this is a pre-1.0 breaking-change window (per #388's framing —
"while breaking pre-1.0"), this is the point to make these renames, not
after 1.0 when they become a deprecation cycle.

## Decision

1. **`prec` → `precision`.** Rename across the numeric metadata (`FloatMeta`,
   transports, docs, snippets) wherever `prec` appears. `precision` stays an
   `int` (decimal places). No behaviour change.

2. **Limits alignment — nested, not flat.** Replace the flat
   `min`/`max`/`min_alarm`/`max_alarm` fields with a nested `Limits` structure
   aligned to the bluesky event-model, so alarm/control/display limits read the
   same way in FastCS and ophyd-async docs. **All four categories** — control,
   display, alarm, warning — are present and **all optional**, with inheritance:

   - supply none ⇒ all unbounded;
   - Display but not Control ⇒ Control inherits Display (for a writeable attr);
   - Alarm but not Warning ⇒ Warning inherits Alarm;
   - both Alarm and Warning ⇒ assert Warning ⊆ Alarm;
   - otherwise unspecified ⇒ unbounded.

3. **`Array1D`/`Table` hint spellings, which are also the runtime structure.**
   Adopt `Array1D[np.int32]` and `Table` as the FastCS *hint* spellings a
   `ControllerFiller`-scanned class body uses. With `DataType` dropped, these
   are **both** the hint and the runtime structure passed around as the
   datatype — there is no separate `Waveform`/table `DataType` object to map to.
   Procedural construction passes the same types plus `*Meta` (e.g.
   `AttrRW(Array1D[np.int32], shape=(4,), getter=...)`), and shape/array
   metadata rides on `Array1DMeta` exactly as `precision`/`units` ride on
   `FloatMeta`.

This is explicitly the smallest naming-pass scope agreed in #388 for 1.0. A
`Prec`/`Units`/`Shape` `Annotated` extras vocabulary (letting a hint carry
precision/units/shape without a spec object) is called out in #388 as a
**post-1.0** option enabled by, but not required by, the
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md) extras
mechanism — not part of this ADR.

## Consequences

- Every driver using `Float(prec=...)`, `.min`/`.max`/`.min_alarm`/
  `.max_alarm` needs a rename to `precision` and the nested `Limits`
  structure. This is a wide diff across all downstream repos (`fastcs-eiger`,
  `fastcs-catio`, `fastcs-secop`, `fastcs-PandABlocks` all use numeric
  limits somewhere); the flat→nested Limits change is structural, not purely a
  rename.
- Transports serving `precision`/limits metadata (EPICS record fields,
  Tango attribute properties, REST/GraphQL schema) read these from `attr.meta`
  ([ADR 14](0014-attribute-io-rw-rework.md)) and need their field-name mapping
  updated to the renamed / nested fields.
- `Array1D`/`Table` become the single array/table representation for both
  hinted and procedural attributes, so there is no hint-vs-runtime mapping
  layer to keep in sync.

## Questions resolved in review (#402)

1. **Flat or nested limits?** Nested — a `Limits` structure, not four flat
   fields.
2. **Which limit categories, and how do they combine?** All four
   (control/display/alarm/warning), all optional, with the inheritance rules in
   Decision point 2 (Control inherits Display, Warning inherits Alarm, assert
   Warning ⊆ Alarm, otherwise unbounded).
3. **Is `precision` an int or a float?** An `int` (decimal places).
4. **Do `Array1D`/`Table` map onto a separate runtime `DataType`?** No — with
   `DataType` dropped they *are* both the hint and the runtime structure; there
   is no `Waveform` object to map to.
5. **Where does this land?** It folds naturally into the per-attribute IO /
   `DataType`-drop PR (#392) — the implementer's choice, not a separate late PR.
