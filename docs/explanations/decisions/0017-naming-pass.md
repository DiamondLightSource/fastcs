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

## Open questions

1. Does the Limits alignment keep four flat fields (just renamed to match
   event-model terms) or restructure into an actual `Limits`-like nested
   object? The latter is a bigger, more disruptive change to
   `DataType.validate` and every downstream driver constructing `Float(...)`
   with keyword limits.
2. Which event-model `Limits` categories does FastCS need —
   control/display/alarm/warning all four, or a subset? EPICS records only
   naturally distinguish alarm vs. display/control limits; does the mapping
   from four FastCS fields to N event-model categories lose or need to
   invent information for some transports?
3. Is `precision` an `int` (decimal places, as `prec` is today) or does
   aligning with event-model conventions change its meaning/type too?
4. For `Array1D`/`Table` hints: is `Array1D[np.int32]` a real usable type at
   both class-definition time (for `ControllerFiller` to scan) and at
   type-checking time (for pyright), or a `TypeAlias`/`Annotated` wrapper
   around `Waveform`? What does the two-way mapping (hint → `ControllerFiller`
   constructs a `Waveform`; introspection-provisioned `Waveform` → does the
   hint still validate it, per decision in
   [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
   open question 1) look like precisely?
5. Should this rename land in the same PR as
   [ADR 14](0014-attribute-io-rw-rework.md) (since both touch `DataType`-
   adjacent code and every downstream driver already has to touch these
   files), or stay a separate, later PR per §8 work-plan ordering (item 5,
   after items 1-4)?
