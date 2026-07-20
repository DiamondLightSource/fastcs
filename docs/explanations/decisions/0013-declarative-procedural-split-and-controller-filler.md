# 13. Declarative/Procedural Split and ControllerFiller

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388)

## Status

Proposed

## Context

FastCS currently has two mechanisms for declaring the shape of a `Controller`:

1. **Class-scope `Attribute` instances** (`ramp_rate = AttrRW(Float(), io_ref=...)`
   assigned directly in the class body). `BaseController._bind_attrs`
   (`src/fastcs/controllers/base_controller.py`) walks the MRO, finds these, and
   `deepcopy`s each one onto the instance so that multiple instances of the same
   `Controller` subclass do not share mutable state.
2. **Bare type hints** (`frames: AttrRW[int]`) validated, not created, by
   `HintedAttribute` (`src/fastcs/attributes/hinted_attribute.py`) via
   `_find_type_hints`/`_validate_type_hints`. The actual `Attribute` must be
   constructed and assigned by the developer, normally in an `initialise()`
   override that introspects a device.

ophyd-async has the equivalent split (`Device` class-body hints vs. `__init__`
procedural construction, see `docs/explanations/declarative-vs-procedural.md`),
but only one mechanism for the declarative half: hints always *create* children,
provisioned by a `DeviceConnector`-owned `DeviceFiller`
(`ophyd_async/core/_device_filler.py`) either immediately or later via
connect-time introspection. There is no ophyd-async equivalent of FastCS's
class-scope instance style, and there cannot be — a `Signal`'s backend depends on
which `DeviceConnector` the owning `Device` is constructed with, so the backend
cannot be known until connect/construction time chooses the connector.

Our own downstream drivers show why the FastCS class-scope-instance mechanism is
already the minority case in practice, not the norm:

- `fastcs-eiger` mixes class-body instances (`trigger_exposure = AttrRW(Float())`)
  with bare hints filled by REST-API introspection in `initialise()`
  (`eiger_detector_controller.py`) — i.e. it already wants one unified mechanism.
- `fastcs-secop`, `fastcs-PandABlocks`, and `fastcs-catio`'s dynamic path build
  **all** of their attributes from wire/YAML-derived data at `initialise()` time;
  none of them use class-scope instances at all.

The `deepcopy` half of `_bind_attrs` exists solely to make class-scope instances
safe to reuse across `Controller` instances. It is fragile (IO objects, bound
callbacks, and connections do not always survive a deepcopy cleanly) and costs
construction time on every instantiation, for a feature none of our real-world
introspecting drivers use.

## Decision

Adopt a single declarative mechanism, matching ophyd-async: **class body =
bare type hints only; instance scope = procedural construction.** Concretely:

- Remove class-scope `Attribute` **instances** entirely. `AttrRW(Float(),
  io=...)` may no longer be assigned directly in a class body.
- Remove the deepcopy half of `_bind_attrs`. Method binding for `@command`/
  `@scan` (the `UnboundCommand`/`UnboundScan` machinery) is unaffected and
  stays, since it does not require deepcopy — see decision 14 (`@attr_rw`
  decorator sugar).
- Remove `HintedAttribute` and `_validate_type_hints`/`_validate_hinted_*` as
  a *separate* validation-only pass. Their job — "this hinted child must
  exist with the right type after initialisation" — is subsumed into the new
  `ControllerFiller`.
- Introduce `ControllerFiller`, a direct structural port of ophyd-async's
  `DeviceFiller`. It scans class-body type hints (`AttrR/W/RW[T]`,
  `Command[P, T]` — see [ADR 15](0015-typed-commands.md), nested `Controller`
  / `ControllerVector[T]`), creates children **unfilled**, and tracks
  filled/unfilled state per child. `check_filled(source)` raises, listing by
  name, anything a `Controller`'s `initialise()` promised via a hint but did
  not provision.
- `ControllerFiller` yields `(child, extras)` for each created child — the
  `extras` being anything else found in an `Annotated[...]` hint — so that
  protocol libraries (a future SCPI package, for example) can define their
  own extras vocabulary the same way ophyd-async's `PvSuffix`/`TangoPolling`
  do. Core FastCS defines **no** extras vocabulary for 1.0 (decision 3 of
  #388).
- The refined rule from decision 14 of #388: *class body = declarations +
  decorated behaviour; instance scope = construction with data.* This keeps
  `@command`/`@scan`, and the new `@attr_r`/`@attr_rw` sugar, as class-body
  citizens, since none of them require per-instance deepcopy — they bind a
  method to `self` at construction time instead.

Example, before and after:

```python
# Before: class-scope instance, deepcopy'd per-instance
class TemperatureRampController(Controller):
    start = AttrRW(Int(), io_ref=TemperatureControllerAttributeIORef(name="S"))

# After: bare hint, filled procedurally
class TemperatureRampController(Controller):
    start: AttrRW[int]

    def __init__(self, index: int, conn: IPConnection) -> None:
        super().__init__()
        suffix = f"{index:02d}"
        self.start = AttrRW(Int(), io=TempIO(conn, "S", suffix))
```

Introspecting controllers (`fastcs-eiger`, `fastcs-secop`,
`fastcs-PandABlocks`, `fastcs-catio`'s dynamic path) keep working exactly as
today's `initialise()` + `add_attribute` pattern, but the fully-dynamic case
where the *set* of attributes is not known until a network round-trip
completes (`fastcs-PandABlocks`, `fastcs-secop`) needs `ControllerFiller` to
support filling children that were never hinted at all — mirroring
`DeviceFiller.fill_child_signal`'s "no annotation existed, introspection
added an undeclared attribute" path. This is a harder requirement than most
of ophyd-async's own connectors exercise (PVI and Tango both fill *some*
undeclared children, but FastCS's dynamic drivers may have **zero** static
hints and still need to build a full attribute tree from nothing) and is
called out below as an open question.

## Consequences

- Every existing driver using class-scope `Attribute` instances needs
  migration to bare hints + `__init__`/`initialise()` construction — see the
  Example 1 (`DRAFT: Example 1 — IORef temperature controller`) and Example
  2 (`DRAFT: Example 2 — introspectable Eiger-style controller`) sub-issues
  of #388, and the corresponding downstream repo work.
- `Controller.__init__` no longer needs to run `_bind_attrs`, simplifying
  construction and removing a source of deepcopy-related bugs.
- Static typing improves: a bare hint `frames: AttrRW[int]` is exactly the
  type a type checker sees, with no deepcopy step that could plausibly
  change it.
- `ControllerFiller` becomes a new stable, documented surface — see
  [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md) for how
  it interacts with the stable `ControllerAPI` surface consumed by the
  embedded ophyd-async connector.

## Open questions

1. Does `ControllerFiller` need to support "no hints exist at all — build the
   entire attribute tree from introspected data" (the `fastcs-PandABlocks`
   and `fastcs-secop` case), or is some minimal static shape (even just a
   marker on the `Controller` subclass) always required? `DeviceFiller` has
   no precedent for the fully-hint-free case.
2. `fastcs-catio`'s dynamic path builds whole controller *classes* at runtime
   via `type(...)` from YAML definitions, before any instance (and hence any
   `ControllerFiller`) exists. Is that pattern still supported, unsupported,
   or does it need to move to instance-level dynamic attribute construction
   under the new model?
3. `fastcs-eiger`'s `OdinController.initialise()` constructs new attributes
   that reference sibling sub-controllers' attributes, assuming those
   sub-controllers already exist. Does `ControllerFiller` impose an
   ordering/dependency mechanism between sibling children, or is this left
   as an `initialise()` implementation detail (call `super().initialise()`
   first)?
4. Should `check_filled` be able to distinguish "this hinted child is
   optional" (ophyd-async's `Optional[X]` convention), or does FastCS treat
   every hint as required for 1.0?
5. Exact `ControllerFiller` method names/signatures are left to the
   prototype — should they mirror `DeviceFiller`'s names 1:1
   (`fill_child_signal` → `fill_child_attribute`?) for discoverability by
   developers who know both libraries, or diverge where FastCS's vocabulary
   (`Attribute` vs `Signal`) differs?
