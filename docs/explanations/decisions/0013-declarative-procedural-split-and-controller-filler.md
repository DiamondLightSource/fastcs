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
declarations + decorated behaviour; instance scope = construction with data.**
Concretely:

- Remove class-scope `Attribute` **instances** entirely. `AttrRW(getter=...,
  setter=...)` may no longer be assigned directly in a class body.
- Remove the deepcopy half of `_bind_attrs`. Method binding for `@command`/
  `@scan` — and the new `@attr`/`@x.setter` sugar
  ([ADR 18](0018-attr-decorator-sugar.md)) — is unaffected and stays, since it
  does not require deepcopy: it binds a method to `self` at construction time
  via the `UnboundCommand`/`UnboundScan` machinery rather than deepcopying a
  prototype.
- Remove `HintedAttribute` and `_validate_type_hints`/`_validate_hinted_*` as
  a *separate* validation-only pass. Their job — "this hinted child must
  exist with the right type after initialisation" — is subsumed into the new
  `ControllerFiller`.
- Introduce `ControllerFiller`, a direct structural port of ophyd-async's
  `DeviceFiller`. It scans class-body type hints (`AttrR/W/RW[T]`,
  `Command[P, T]` — see [ADR 15](0015-typed-commands.md) — and nested
  `Controller` / `ControllerVector[T]`), creates children **unfilled**, and
  tracks filled/unfilled state per child. `check_filled(source)` raises,
  listing by name, anything a `Controller`'s `initialise()` promised via a hint
  but did not provision.
- `ControllerFiller` yields `(child, extras)` for each created child — the
  `extras` being anything else found in an `Annotated[...]` hint — so that
  protocol libraries (a future SCPI package, for example) can define their
  own extras vocabulary the same way ophyd-async's `PvSuffix`/`TangoPolling`
  do. Core FastCS defines **no** extras vocabulary for 1.0 (decision 3 of
  #388).
- When a child is filled from an `Annotated[AttrRW[T], extras]` hint, the filler
  **runtime-validates** the metadata the extras carries (a `FloatMeta`, or a
  protocol object's `.meta` such as `SCPIParam(...).meta`) against the datatype
  `T` — e.g. `precision` supplied for a `str` raises. This is the runtime
  counterpart to the static `Unpack[FloatMeta]` check on the procedural `Attr*`
  constructors (see [ADR 14](0014-attribute-io-rw-rework.md)).

Two patterns follow, and the class body distinguishes them.

**Procedural, no hint** — the value is fully constructed in `__init__`, so it
needs no class-body declaration at all. Per-attribute IO is a `getter`/`setter`
pair of callables ([ADR 14](0014-attribute-io-rw-rework.md)); the datatype is
inferred from the getter's return annotation:

```python
class TemperatureRampController(Controller):
    def __init__(self, index: int, conn: IPConnection) -> None:
        super().__init__()
        suffix = f"{index:02d}"

        async def get_start() -> int:
            return int(await conn.send_query(f"S{suffix}?\r\n"))

        async def set_start(value: int) -> None:
            await conn.send_command(f"S{suffix}={value}\r\n")

        # datatype int is inferred from get_start's return annotation
        self.start = AttrRW(getter=Polled(get_start, period=0.2), setter=set_start)
```

**Declarative hint + filler** — the value is *promised* by a hint; the
`ControllerFiller` (run from `Controller.__init__`) creates it as an
**unfilled** `Attribute` so it **exists as soon as `__init__` returns**, and
`initialise()` later *fills* it (provisions the getter/setter + metadata) by
introspection:

```python
class OdinDetector(Controller):
    frames: AttrRW[int]     # created UNFILLED by the filler in __init__;
                            # self.frames EXISTS after __init__, before initialise()

    async def initialise(self) -> None:
        # introspection FILLS the already-created hinted attrs (getter/setter +
        # metadata), and may add wholly-undeclared dynamic attrs (no hint)
        for name, spec in await self._query_parameter_tree():
            self.filler.fill_attribute(name, spec)   # validates meta vs datatype
        self.filler.check_filled()
```

**The rule** (identical to ophyd-async's rule for `Signal`s): *at the end of
`__init__`, any Attribute referenced in code — and therefore carrying a type
hint — must exist* (the filler guarantees this for hinted children by creating
them unfilled during `__init__`). Only `__init__` is serial; `initialise()`
may then run in parallel across controllers.

Introspecting controllers keep working as today's `initialise()` +
`add_attribute` pattern. The fully-dynamic case — where the *set* of
attributes is not known until a network round-trip completes
(`fastcs-PandABlocks`, `fastcs-secop`) — needs `ControllerFiller` to fill
children that were never hinted at all. This is **no harder than ophyd-async
already supports**: `Device(connector=PviConnector(prefix))` fills a whole
`Signal` tree from introspection with no hints required, and `ControllerFiller`
mirrors that `DeviceFiller` path directly.

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
  embedded ophyd-async connector ([ADR 19](0019-embedded-ophyd-async-connector.md)).

## Questions resolved in review (#402)

1. **Must `ControllerFiller` support "no hints at all"?** Yes — it must build
   the whole attribute tree from introspected data with nothing declared in the
   class body (`fastcs-PandABlocks`, `fastcs-secop`), exactly as
   `DeviceFiller` does for a `PviConnector`.
2. **Is `fastcs-catio`'s runtime `type(...)` class-building supported?** No. A
   bare `Controller` instead allows attributes to be added onto it from the
   outside — which is exactly what the fillers do — so catio moves to
   instance-level dynamic attribute construction.
3. **Is there a sibling-ordering mechanism?** No. The rule "any hint-referenced
   Attribute must exist by the end of `__init__`" makes `initialise()`
   parallelisable; sibling dependencies are an `initialise()` implementation
   detail (call `super().initialise()` first).
4. **Are `Optional[X]` hints supported?** Yes — `check_filled` treats an
   optional hint as not-required.
5. **Do we follow `DeviceFiller`'s names?** Follow its *structure*, not its
   names. Architectural similarity matters; method names match only where
   FastCS's vocabulary (`Attribute` vs `Signal`) makes them fit.
