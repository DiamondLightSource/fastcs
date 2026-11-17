# 14. Per-Attribute IO as getter/setter Callables

Date: 2026-07-20 (revised 2026-08-03, after the #412 review)

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388),
[ADR 9](0009-handler-to-attribute-io-pattern.md), [ADR 12](0012-attribute-io-naming-convention.md),
[ADR 18](0018-attr-decorator-sugar.md), [ADR 20](0020-transport-setpoint-mirroring.md)

## Status

Proposed

## Context

[ADR 9](0009-handler-to-attribute-io-pattern.md) split the old `Handler`
pattern into `AttributeIO` (behaviour, one instance per `Controller`,
shared across attributes) and `AttributeIORef` (per-attribute resource
specification, dispatched to the right `AttributeIO` by type at
`_connect_attribute_ios` time). Its sole structural justification was that
class-scope `Attribute` instances are created before `__init__` runs, so
they cannot close over a live connection — the `AttributeIORef` only needed
to carry inert data (a register name, a URI) until the matching
`AttributeIO` was found by type at `post_initialise()`.

[ADR 13](0013-declarative-procedural-split-and-controller-filler.md) removes
class-scope `Attribute` instances entirely. Every attribute is now
constructed procedurally, in `__init__` or `initialise()`, where a live
connection is already in scope. The situation `AttributeIORef` was invented
to solve no longer exists.

We also verified downstream that nothing outside FastCS consumes `io_ref` or
the IO registry directly — every consumer bottoms out in
`attr.set_on_put_callback(io.send)` / `attr.set_update_callback(io.update)`
(`base_controller.py:_connect_attribute_ios`), i.e. the ref/registry split is
a dispatch layer over callbacks that already exist as plain methods.

The dispatch-by-type registry has real costs in our downstream drivers:

- `fastcs-catio` registers **three** separate `AttributeIO`/`AttributeIORef`
  pairs on one `Controller` (`ios=[poll_io, symbol_io, coe_io]`) purely so
  each attribute's `io_ref` type can select the right one at
  `_connect_attribute_ios` time — indirection that a direct per-attribute
  callable removes outright.
- `fastcs-secop` needs a private escape hatch,
  `attr._call_sync_setpoint_callbacks`, to push setpoint echoes from its
  `send()` implementation, because the current `AttributeIO.send` signature
  has no sanctioned way to do this — flagged in-code as pending a public API.
- `fastcs-PandABlocks`'s `UnitsIO.send` mutates a *sibling* attribute's
  datatype (`attribute_to_scale.update_datatype(...)`), reaching outside the
  attribute it was invoked for — a pattern the new IO shape should not make
  harder, even though it stays an edge case.

## Decision

Delete `AttributeIO` and `AttributeIORef` and their whole dispatch machinery.
Per-attribute IO is supplied as plain **`getter`/`setter` callables** on the
`Attr*` constructors — which *is* the procedural spelling of the `@attr`
decorator ([ADR 18](0018-attr-decorator-sugar.md)):

- `AttrR(getter=g)`, `AttrW(setter=s)`, `AttrRW(getter=g, setter=s)`. Access
  mode is enforced by **which parameters exist** (an `AttrR` has no `setter`),
  so there is no IO class hierarchy and no abstract-method enforcement to
  carry — and `getter=` on a read-only attr is honest where `io=` was a false
  friend.
- **The getter returns the value; the framework applies it** —
  `getter() -> T | Update[T]` — instead of the old imperative `io.update(attr)`.
  Imperative / multi-attribute periodic logic stays with `@scan`, which is
  *why* per-attribute IO shrinks to "one value in / out".
- The **setter** returns `None | T | Update[T]`: `None` = fire-and-forget
  (readback catches up on the next poll / the setpoint cache); a returned value
  is the device's *accepted* value (a clamp or echo) and updates the readback +
  the setpoint cache immediately — the sanctioned replacement for
  `fastcs-secop`'s private `_call_sync_setpoint_callbacks`.
- **Datatype is optional when a getter/setter is given** — inferred from the
  getter's return annotation (or the setter's parameter), unwrapping `Update[T]`
  to `T`, so `AttrR(getter=g)` yields `AttrR[float]` with no restated type
  (parity with `@attr`). Only the bare python type is optional;
  `precision`/`units`/… stay explicit kwargs, and the per-datatype
  `Unpack[*Meta]` static check keys off the inferred return type. Not inferable
  (`-> Any`, an unannotated lambda) ⇒ the positional datatype is required
  (fail-fast at construction).
- Soft is now simply the *absence* of a getter/setter (`AttrRW(float)`
  self-wires setpoint→readback as before, the analogue of ophyd-async's
  `soft_signal_rw`); the old `io=None` sentinel is gone.
- The declarative/filler path lowers to the **same** getter/setter (a
  `SCPIController`'s filler builds the callables from a `SCPIParam`). getter and
  setter are where the old `_connect_attribute_ios` wiring now lives, so
  transports and the embedded connector are unaffected.

`attr` is a **decorator only** (`@attr` / `@attr(precision=3)` +
`@voltage.setter`, [ADR 18](0018-attr-decorator-sugar.md)); there is no
free-function `attr()` factory — the procedural spelling is `AttrR`/`AttrRW`
directly.

```python
class TemperatureRampController(Controller):
    def __init__(self, index: int, conn: IPConnection) -> None:
        super().__init__()
        name = f"R{index:02d}"

        async def get_ramp_rate() -> float:
            return float(await conn.send_query(f"{name}?\r\n"))

        async def set_ramp_rate(value: float) -> None:
            await conn.send_command(f"{name}={value}\r\n")

        # datatype float inferred from get_ramp_rate's return annotation
        self.ramp_rate = AttrRW(
            getter=Polled(get_ramp_rate, period=0.2),
            setter=set_ramp_rate,
            units="deg",
        )
```

### The reading schedule travels with the getter

There is no `poll_period` constructor argument. A getter carries its own
schedule, so the two cannot drift apart and the pair can be passed around as
one value:

```python
self.config   = AttrR(float, getter=self._get_config)                       # once, at connect
self.reading  = AttrR(float, getter=Polled(self._get_reading, period=0.2))  # every 0.2s
self.label    = AttrR(str, getter=NotPolled(self._get_label))               # never; poll() only
self.computed = AttrR(float)                                                # soft, no getter
```

`Polled` and `NotPolled` take an optional getter and bind one when called, so
the same objects serve the declarative spelling in
[ADR 18](0018-attr-decorator-sugar.md) — where the getter arrives by decoration
and there is no argument to wrap — giving one vocabulary across both:

| Schedule | Procedural | Declarative |
|---|---|---|
| Once, at connect | `AttrR(t, getter=g)` | `@attr(units="V")` |
| Every 0.5s | `AttrR(t, getter=Polled(g, period=0.5))` | `@attr(Polled(0.5), units="V")` |
| Never; `poll()` only | `AttrR(t, getter=NotPolled(g))` | `@attr(NotPolled(), units="V")` |

**A bare getter means "read once, at connect"**, not "never read". Three
defaults were considered:

1. *Bare = once* (chosen). Fails safe: an attribute always shows a real value,
   and polling is opted into per attribute rather than being something you must
   remember to switch off.
2. *Bare = never read.* Restores the pre-refactor `AttributeIORef.update_period
   = None` default and makes all scheduling explicit — but fails **silently**:
   an unpolled `AttrRW` sits at the datatype default and, under
   [ADR 20](0020-transport-setpoint-mirroring.md), never establishes a setpoint
   either, so every transport shows `0`/`""`/`False` until someone writes to it.
3. *No default; always require a wrapper.* Rejected because ADR 18 promises a
   bare `@attr`, which must resolve to some schedule. A constructor that refused
   to default while the decorator defaulted would reintroduce the asymmetry
   these wrappers exist to remove.

`ONCE` (`float("inf")`) survives internally as what `poll_period` reports for
the bare case, but a driver author never spells it: `Polled(getter,
period=ONCE)` would read as a contradiction, and the once-only case is the one
with no wrapper at all. `NotPolled(g)` is distinct from having no getter — the
former is still readable via `await attr.poll()` and by transports on demand,
the latter has nothing to read.

### `Update[T]`

`Update` is what a getter or setter returns when a bare value is not enough:

```python
@dataclass
class Update(Generic[T]):
    readback: T
    timestamp: float | None = None   # None ⇒ framework stamps receive-time
    setpoint: T | None = None        # None ⇒ leave the cached setpoint alone
```

- `readback` is the value, named for the cache it feeds.
- `setpoint` is how a device that reports its own setpoint drives one, and how
  a setter distinguishes "the device clamped the value it will *report*" from
  "the device clamped what I *asked for*". A **bare** value returned from a
  setter means both — it is equivalent to `Update(readback=v, setpoint=v)`.
- `severity` is **not** on `Update` yet; native timestamps and the severity
  enum are [ADR 16](0016-setpoint-cache-timestamps-and-controller-runner.md)'s
  scope and land with it. `timestamp` is accepted here so the field ordering is
  settled, but is not yet persisted.

### Runtime surface

The old `get()` / `update(value)` / `put(value)` method trio is renamed and
split so that both **access mode** and **whether a call touches the device**
are legible from the member set:

| Member | Kind | AttrR | AttrW | AttrRW | Device IO? |
|---|---|---|---|---|---|
| `.readback` | property (sync) | ✓ | — | ✓ | no (cached) |
| `.setpoint` | property (sync) | — | ✓ | ✓ | no (cached) |
| `poll()` | async method | ✓ | — | ✓ | **yes** (getter) |
| `update(value)` | async method | ✓ | — | ✓ | no (cache push) |
| `update_setpoint(value)` | async method | — | ✓ | ✓ | no (cache push) |
| `set(value)` | async method | — | ✓ | ✓ | **yes** (setter) |
| `add_readback_callback()` | method | ✓ | — | ✓ | no |
| `add_setpoint_callback()` | method | — | ✓ | ✓ | no |

- **`.readback` / `.setpoint` replace `.value`.** Two explicitly-named cached
  properties instead of one whose meaning shifted per class. Each class exposes
  only the ones it has (`AttrR` has no `.setpoint`, `AttrW` no `.readback`), so
  access mode reads off the surface — and the pair mirrors bluesky /
  ophyd-async's `Location(setpoint, readback)` exactly, so `AttrRW` maps 1:1
  onto `locate()` and the embedded connector's `get_value`/`get_setpoint`. Both
  are **read-only** properties: writes are async (validate + `await` callbacks)
  and so cannot be property setters.
- **`poll()` replaces the no-arg `update()`; `update_period` → `poll_period`.**
  `poll()` does a live getter read, caches it, and **returns** the value (so an
  on-demand read is `await attr.poll()`, mirroring ophyd's live `get_value()`);
  `poll_period` is now a read-only property reporting the schedule resolved from
  the getter's wrapper, not a constructor argument. This deletes the
  `set_update_callback` / `bind_update_callback` plumbing — the getter lives on
  the attr and `poll()` calls it.
- **`update(value)` is now purely a cache push** — a `value` or `Update[T]`
  from a `@scan`/subscription — with no device IO and no `None` sentinel.
  `update_setpoint(value)` is its setpoint-side counterpart.
- **`set(value)` replaces `put()`** (the bluesky/ophyd verb): it caches
  `.setpoint` immediately (decision 10a) and publishes it to the setpoint
  callbacks, then runs the setter; the setter's return feeds `.readback` via
  `update()`. The old `sync_setpoint=` kwarg and `_call_sync_setpoint_callbacks`
  are gone.
- **The two callback registrars are symmetric.** `add_readback_callback()`
  (formerly `add_on_update_callback()`) and `add_setpoint_callback()` are how
  transports publish each cache. Transports must not track a setpoint of their
  own — see [ADR 20](0020-transport-setpoint-mirroring.md), which also removes
  the per-transport "seeding" of a setpoint display by making the first readback
  on an `AttrRW` establish the setpoint.

So `poll()`/`set()` touch the device; `.readback`/`.setpoint`/`update()` do
not. `Attribute` also loses its second generic parameter —
`Attribute[DType_T, AttributeIORefT]` collapses to `Attribute[DType_T]`, making
`AttrRW[float]` structurally isomorphic to ophyd-async's `SignalRW[float]`.

### Datatype metadata: the `*Meta` TypedDicts

`DataType` classes are gone ([ADR 15](0015-typed-commands.md) /
[ADR 17](0017-naming-pass.md)). The metadata they carried (precision, units,
nested limits, …) moves to a per-datatype `TypedDict` — `FloatMeta`, `IntMeta`,
`StrMeta`, `BoolMeta`, `EnumMeta`, `Array1DMeta`, `TableMeta` — and **the
resolved metadata is stored on the `Attribute` itself** (`attr.meta`), not on a
separate datatype object. Every transport/connector that read
`attr.datatype.precision`/`.units`/`.limits`/`.choices` now reads `attr.meta`
(enum `choices` come from the python type; `EnumMeta` is display-only).

Two spellings, two validation layers:

- **Procedural (statically checked):** the `Attr*` constructors are overloaded
  per datatype so the right `*Meta` is unpacked into `**kwargs`:

  ```python
  # conceptually, one overload per datatype:
  def AttrRW(dtype: type[float], *, getter=..., setter=...,
            **kwargs: Unpack[FloatMeta]) -> AttrRW[float]: ...

  self.temperature = AttrRW(float, precision=3, units="deg", setter=apply_temp)
  # AttrRW(str, precision=3) is a static type error
  ```

  (The `dtype` positional is only needed when it cannot be inferred from a
  getter/setter annotation, as above.)

- **Declarative (runtime-checked by the filler):**
  `Annotated[AttrRW[float], FloatMeta(precision=3)]` (rare) or
  `Annotated[AttrRW[float], SCPIParam("P", precision=3)]` (common). Neither
  ties the metadata to the `AttrRW[...]` type param statically, so
  [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s
  `ControllerFiller` validates it against the datatype at fill time. A generic
  extras object takes the **superset** `Meta` TypedDict —
  `SCPIParam(param: str, **kwargs: Unpack[Meta])` (`Meta` being the union of
  `FloatMeta`/`StrMeta`/… fields, all optional) — stores a `.meta`, and the
  filler passes that `.meta` into the constructed `AttrRW`.

**One spec object per declaratively-filled attribute.** A protocol extra like
`SCPIParam` is the *single place* an attribute's whole specification is
written: both the protocol binding (the command token, `"P"`) and all its
generic metadata (`description`, `precision`, `units`, limits…) via
`**Unpack[Meta]`. The filler treats that extra as the **exclusive** spec
source for its attribute — it does **not** also merge a separate
`FloatMeta`/`Meta` extra sitting on the same `Annotated[...]` hint, so there
is no precedence question to resolve. The trade this accepts is deliberate:
routing metadata through the superset `Meta` (not a per-datatype
`Unpack[FloatMeta]`) means correctness is the filler's **runtime** job, not a
static check — a separate `Annotated` extra cannot tie its `**Meta` to the
`AttrRW[...]` datatype param, and making the extra generic
(`SCPIParam[float](...)`) only forces the user to restate a type already in
the hint. So the declarative path pays for its ergonomics with runtime
validation; the filler's error must name the attribute and field (e.g.
"`precision` is not valid for `str` attribute `device_id`").

Naming: the extra is `SCPIParam` (a binding object you *instantiate* as an
`Annotated` extra), **not** `SCPIMeta` — the `*Meta` suffix is reserved for
the metadata TypedDicts you `Unpack` (`FloatMeta`, `Meta`), a different kind
of Python object. `SCPIParam` is a sibling of ophyd-async's
`PvSuffix`/`TangoPolling`, not of `SignalMetadata`. It is **not** part of core
FastCS (decision 3: core defines no extras vocabulary for 1.0) — it lives in a
protocol layer; the demo package ships an example `SCPIController` +
`SCPIParam` to show how a third party builds one on the filler's
`(child, extras)` mechanism. The `*Meta` module location is deferred to the
public-API-namespace decision (#406); land it provisionally until then.

### Migration

Migration collapses an `AttributeIO`/`AttributeIORef` pair into two callables:
the old `update`/`send` method bodies become the `getter`/`setter`, constructed
once per attribute instead of once per controller.

```python
# Before (ADR 9 shape)
class TempIORef(AttributeIORef):
    name: str

class TempIO(AttributeIO[float, TempIORef]):
    async def update(self, attr: AttrR[float, TempIORef]) -> None:
        resp = await self._conn.send_query(f"{attr.io_ref.name}?\r\n")
        await attr.update(float(resp))

    async def send(self, attr: AttrW[float, TempIORef], value: float) -> None:
        await self._conn.send_command(f"{attr.io_ref.name}={value}\r\n")

ramp_rate = AttrRW(Float(), io_ref=TempIORef(name="R"))
# ... elsewhere: Controller(ios=[TempIO(conn)])

# After
def temp_io(conn: IPConnection, name: str):
    async def getter() -> float:
        return float(await conn.send_query(f"{name}?\r\n"))

    async def setter(value: float) -> None:
        await conn.send_command(f"{name}={value}\r\n")

    return getter, setter

get_ramp, set_ramp = temp_io(conn, "R")
self.ramp_rate = AttrRW(getter=Polled(get_ramp, period=0.2), setter=set_ramp)
```

The old ref's `update_period=0.2` becomes the `Polled(..., period=0.2)` wrapper;
a ref that left `update_period` at its `None` default becomes `NotPolled(...)`
if it really should never be read, or a bare getter if a connect-time read was
what it wanted.

`fastcs-catio`'s three-IO-per-controller pattern becomes per-attribute
callables with no registry needed at all. `fastcs-secop`'s private
`_call_sync_setpoint_callbacks` call is replaced by a value-returning setter.

## Consequences

- Every driver that declared `AttributeIO`/`AttributeIORef` subclasses migrates
  their `update`/`send` bodies into `getter`/`setter` callables — see the
  affected §9 files in the sub-issues of #388 (`attributes/`,
  `controllers/base_controller.py`, `controllers/controller.py`) and the
  corresponding downstream repo issues. The migration is mechanical.
- `Attribute` loses its second generic parameter, simplifying every type
  hint in downstream code (`AttrR[float, MyRef]` → `AttrR[float]`).
- Access-mode compatibility is enforced by the parameter set — an `AttrR` has
  no `setter`, so there is no `_validate_io` runtime check and no way to attach
  a read-only IO to a write-capable attr statically. For the dynamically-built
  `Any`-typed case (`fastcs-secop`, `fastcs-PandABlocks`) a runtime check at
  `post_initialise` still catches a missing setter on a write-capable attr.
- The IO no longer has a place to hang per-attribute metadata that
  `fastcs-catio` used to read off `attribute.io_ref`; `attr.meta` and the
  attribute's own attributes replace that access.
- Drivers that relied on the old ref default of `update_period=None` change
  behaviour if they migrate to a bare getter: they gain a connect-time read.
  This is intended (see the three options above) but is the one migration step
  that is not purely mechanical.

## Questions resolved in review (#402, #412)

1. **What replaces the `io=` object and the `ReadIO`/`WriteIO`/`ReadWriteIO`
   hierarchy?** Plain `getter`/`setter` callables on the constructors. The IO
   class hierarchy and its abstract-method enforcement are dropped entirely;
   access mode is enforced by which parameters exist.
2. **Do we still need a runtime access-mode check?** Yes, in addition to the
   static shape: a runtime check (e.g. at `post_initialise`) catches a missing
   setter on a write-capable `Attr` for the dynamically-built `Any`-typed case.
3. **What is the public replacement for `fastcs-secop`'s
   `_call_sync_setpoint_callbacks`?** A `setter` returning `T | Update[T]` *is*
   the sanctioned setpoint echo — the returned value updates the readback and
   the setpoint cache.
4. **Are there `CallbackReadIO`/`CallbackWriteIO` classes in core?** No. The
   one-off callback case folds into `@attr` / `AttrR(getter=…)`
   ([ADR 18](0018-attr-decorator-sugar.md)); the same spelling covers the
   read-only and read/write cases.
5. **How is per-attribute IO metadata recovered from outside `getter`/`setter`?**
   Through `attr.meta` and the attribute's own public members, replacing
   `fastcs-catio`'s `attribute.io_ref` access.
6. **Is the setpoint echo a cross-transport "instantly visible" guarantee?**
   (@Tom-Willemsen / @shihab-dls.) It is now. Caching `.setpoint` before running
   the setter was originally an *attribute-cache* guarantee only, with the
   remote-client view left transport-dependent: **PVA** posted the setpoint as
   soon as it was written, whereas **CA** posted only *after* the update callback
   completed, so a long-running setter delayed the CA-visible setpoint. The
   follow-up this left open is closed by
   [ADR 20](0020-transport-setpoint-mirroring.md): every transport now mirrors
   the attribute's setpoint through `add_setpoint_callback()`, which fires
   before the setter runs, so CA and PVA agree and the ordering is a property of
   the attribute rather than of each transport.
7. **Should `poll_period` be a second constructor argument?** No — merged into
   the getter as `Polled`/`NotPolled` wrappers, so a getter and its schedule are
   one value and the same vocabulary works in the `@attr` decorator, where there
   is no getter argument to pair it with.
