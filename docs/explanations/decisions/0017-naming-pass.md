# 17. Naming Pass: precision, Limits Alignment, Array1D/Table Hints

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

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

Since this is a pre-1.0 breaking-change window (per #388's framing —
"while breaking pre-1.0"), this is the point to make these renames, not
after 1.0 when they become a deprecation cycle.

## Decision

> **Review update (#402): `DataType` is dropped** (see
> [ADR 15](0015-typed-commands.md)). The renames below now live on python
> types + `*Meta` typed dicts, not on `DataType` classes, and this pass folds
> into the `AttributeIO` rework ([ADR 14](0014-attribute-io-rw-rework.md) /
> issue #392) rather than a separate late PR. `Array1D`/`Table` become *both*
> the hint and the runtime structure passed around as the datatype — there is
> no separate `Waveform`/`DataType` object to map to.

1. **`prec` → `precision`.** Rename across `_Numeric`/`Float`/wherever
   `prec` appears (transports, docs, snippets). No behaviour change.
2. **Limits alignment.** Align `min`/`max`/`min_alarm`/`max_alarm` naming
   with event-model `Limits` naming. This ADR records the *intent*
   (converge with bluesky event-model naming so alarm/control/display limits
   read the same way in FastCS and ophyd-async docs); the exact target
   shape (keep four flat fields renamed, or restructure into a `Limits`-like
   object) is an open question for the prototype, since it interacts with
   how `DataType.validate` currently accesses these fields directly as
   dataclass attributes.
3. **`Array1D`/`Table` hint spellings.** Adopt `Array1D[np.int32]` and
   `Table` as the FastCS *hint* spellings a `ControllerFiller`-scanned class
   body uses, mapping internally to the existing `Waveform`/table `DataType`
   runtime objects (constructed the same way as today via
   `AttrRW(Waveform(np.int32, shape=(4,)), io=...)` in procedural code) —
   the hint is sugar for `ControllerFiller`'s type-hint scan, not a
   replacement for the runtime `DataType` classes, matching decision 7 of
   #388 (`DataType` classes stay as the procedural/runtime value; hints are
   what `ControllerFiller` reads).

This is explicitly the smallest naming-pass scope agreed in #388 for 1.0. A
`Prec`/`Units`/`Shape` `Annotated` extras vocabulary (letting a hint carry
precision/units/shape without a full `DataType` instance) is called out in
#388 as a **post-1.0** option enabled by, but not required by, the
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md) extras
mechanism — not part of this ADR.

## Consequences

- Every driver using `Float(prec=...)`, `.min`/`.max`/`.min_alarm`/
  `.max_alarm` needs a mechanical rename. This is a wide, shallow diff
  across all downstream repos (`fastcs-eiger`, `fastcs-catio`,
  `fastcs-secop`, `fastcs-PandABlocks` all use `Float`/numeric limits
  somewhere) but not a structural one, unless the Limits restructuring
  (open question 2) turns out to be more than a rename.
- Transports serving `precision`/limits metadata (EPICS record fields,
  Tango attribute properties, REST/GraphQL schema) need their field-name
  mapping updated to read from the renamed dataclass fields.
- `Array1D`/`Table` hint spellings only affect declarative (hinted)
  attribute declarations; procedural construction with `Waveform(...)`/
  `Table(...)` DataType instances is unchanged.

## Resolved in review (#402)

1. **Limits are nested**, not four flat fields.
2. **All four categories (control/display/alarm/warning), all optional**, with
   inheritance: supply none ⇒ all unbounded; Display but not Control ⇒ Control
   inherits Display (for writeable); Alarm but not Warning ⇒ Warning inherits
   Alarm; both ⇒ assert Warning ⊆ Alarm; otherwise unspecified ⇒ unbounded.
3. **`precision` stays an `int`** (decimal places).
4. **`Array1D` is both the hint and the runtime structure** — with `DataType`
   dropped it falls out in the wash; there is no `Waveform` object to map to.
5. **Where it lands is the implementer's choice** — folds naturally into the
   `AttributeIO`/DataType-drop PR (#392).
