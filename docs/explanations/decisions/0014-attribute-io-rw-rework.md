# 14. AttributeIO R/W/RW Rework and Removal of AttributeIORef

Date: 2026-07-20

**Related:** [Issue #388](https://github.com/DiamondLightSource/fastcs/issues/388),
[ADR 9](0009-handler-to-attribute-io-pattern.md), [ADR 12](0012-attribute-io-naming-convention.md)

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
  `_connect_attribute_ios` time — indirection that a direct `io=` argument
  removes outright.
- `fastcs-secop` needs a private escape hatch,
  `attr._call_sync_setpoint_callbacks`, to push setpoint echoes from its
  `send()` implementation, because the current `AttributeIO.send` signature
  has no sanctioned way to do this — flagged in-code as pending a public API.
- `fastcs-PandABlocks`'s `UnitsIO.send` mutates a *sibling* attribute's
  datatype (`attribute_to_scale.update_datatype(...)`), reaching outside the
  attribute it was invoked for — a pattern the new IO shape should not make
  harder, even though it stays an edge case.

## Decision

> **Review update (#402, 2026-07-22): `io=` objects replaced by `getter`/`setter` callables.**
> The `ReadIO`/`WriteIO`/`ReadWriteIO` hierarchy and the `io=` argument described
> below are **superseded.** Per-attribute IO is supplied as plain callables on the
> constructors, which *is* the procedural spelling of the `@attr` decorator
> ([ADR 18](0018-attr-decorator-sugar.md)):
>
> - `AttrR(getter=g)`, `AttrW(setter=s)`, `AttrRW(getter=g, setter=s)`. Access mode
>   is enforced by **which parameters exist** (an `AttrR` has no `setter`), so the
>   three-class IO hierarchy and its abstract-method enforcement are dropped
>   entirely — and `getter=` on a read-only attr is honest where `io=` was a false
>   friend.
> - **The getter returns the value; the framework applies it** — `getter() -> T |
>   Update[T]` — instead of the old imperative `io.update(attr)`. Imperative /
>   multi-attribute periodic logic stays with `@scan`, which is *why* per-attribute
>   IO shrinks to "one value in / out". The **setter** returns `None | T |
>   Update[T]`: `None` = fire-and-forget (readback catches up on the next poll / the
>   setpoint cache); a returned value is the device's *accepted* value (clamp/echo)
>   and updates the readback + `AttrW` setpoint cache immediately — the sanctioned
>   replacement for `fastcs-secop`'s private `_call_sync_setpoint_callbacks`.
> - `Update[T]` = `value: T`, `timestamp: float | None` (epoch seconds; `None` ⇒
>   framework stamps receive-time), `severity: Severity = OK` (the decision-10b
>   severity enum); used for both the getter return and a value-returning setter —
>   this is how device-native timestamps/severity reach `attr.update()`.
> - **Datatype is optional when a getter/setter is given** — inferred from the
>   getter's return annotation (or the setter's param), unwrapping `Update[T]` to
>   `T`, so `AttrR(getter=g)` yields `AttrR[float]` with no restated type (parity
>   with `@attr`). Only the bare python type is optional; `precision`/`units`/… stay
>   explicit kwargs, and the per-datatype `Unpack[*Meta]` static check keys off the
>   inferred return type. Not inferable (`-> Any`, unannotated lambda) ⇒ the
>   positional datatype is required (fail-fast at construction).
> - `update_period` is a read-side kwarg: `ONCE` = read once at connect (the default
>   when a getter is given); a float = poll at that rate; `None` = **on-demand only**
>   (read when a client asks, never auto-polled). **No getter** = soft, value pushed
>   via `attr.update()` from a `@scan`/callback.
> - Soft is now simply the *absence* of getter/setter (`AttrRW(float)` self-wires
>   setpoint→readback as before); the `io=None` sentinel is gone.
> - The declarative/filler path lowers to the **same** getter/setter (a
>   `SCPIController`'s filler builds the callables from `SCPIParam`); getter/setter
>   are where the old `_connect_attribute_ios` wiring now lives, so transports and
>   the embedded connector are unaffected.
>
> `attr` is a **decorator only** (`@attr` / `@attr(precision=3)` + `@my_attr.setter`);
> there is no free-function `attr()` factory — the procedural spelling is `AttrR`/
> `AttrRW` directly. The `io=` prose below is kept for the `AttributeIORef`→callable
> migration context; read `getter=`/`setter=` for the final shape.

### Runtime surface (review update, 2026-07-22)

The `get()` / `update(value)` / `put(value)` method trio is renamed and split so
that both **access mode** and **whether a call touches the device** are legible
from the member set:

| Member | Kind | AttrR | AttrW | AttrRW | Device IO? |
|---|---|---|---|---|---|
| `.readback` | property (sync) | ✓ | — | ✓ | no (cached) |
| `.setpoint` | property (sync) | — | ✓ | ✓ | no (cached) |
| `poll()` | async method | ✓ | — | ✓ | **yes** (getter) |
| `update(value)` | async method | ✓ | — | ✓ | no (cache push) |
| `set(value)` | async method | — | ✓ | ✓ | **yes** (setter) |

- **`.readback` / `.setpoint` replace `.value`.** Two explicitly-named cached
  properties instead of one whose meaning shifted per class. Each class exposes
  only the ones it has (AttrR has no `.setpoint`, AttrW no `.readback`), so
  access mode reads off the surface — and the pair mirrors bluesky / ophyd-async's
  `Location(setpoint, readback)` exactly, so `AttrRW` maps 1:1 onto `locate()`
  and the embedded connector's `get_value`/`get_setpoint`. Both are **read-only**
  properties: writes are async (validate + `await` callbacks) and so cannot be
  property setters.
- **`poll()` replaces the no-arg `update()`; `update_period` → `poll_period`.**
  `poll()` does a live getter read, caches it, and **returns** the value (so an
  on-demand read is `await attr.poll()`, mirroring ophyd's live `get_value()`);
  `poll_period` (`ONCE` / float / `None`) is only the *schedule* the framework
  calls it on. This deletes the `set_update_callback` / `bind_update_callback`
  plumbing — the getter lives on the attr and `poll()` calls it.
- **`update(value)` is now purely a cache push** — a `value` or `Update[T]` from a
  `@scan`/subscription — with no device IO and no `None` sentinel.
- **`set(value)` replaces `put()`** (the bluesky/ophyd verb): it caches
  `.setpoint` immediately (decision 10a), then runs the setter; the setter's
  `T | Update[T]` return feeds `.readback` via `update()`. The old
  `sync_setpoint=` kwarg and `_call_sync_setpoint_callbacks` are gone. (Caching
  `.setpoint` first is an *attribute-cache* guarantee; *when a remote client sees
  it* is transport-dependent and differs between CA and PVA — see *Resolved in
  review* below.)

So `poll()`/`set()` touch the device; `.readback`/`.setpoint`/`update()` do not.

Replace `AttributeIO`/`AttributeIORef` with three focused, per-attribute IO
base classes with abstract `update`/`send` methods, passed as a single `io=`
constructor argument:

```python
class ReadIO(Generic[DType_T], ABC):
    def __init__(self, update_period: float | None = None): ...

    @abstractmethod
    async def update(self, attr: AttrR[DType_T]) -> None: ...


class WriteIO(Generic[DType_T], ABC):
    @abstractmethod
    async def send(self, attr: AttrW[DType_T], value: DType_T) -> None: ...


class ReadWriteIO(ReadIO[DType_T], WriteIO[DType_T], ABC): ...
```

(Working names per #388; exact naming — `ReadIO`/`WriteIO`/`ReadWriteIO` vs.
`AttrRIO`/`AttrWIO`/`AttrRWIO` — is an open question below and in
[ADR 17](0017-naming-pass.md).) Concrete IO classes are **dataclasses**, so
per-attribute fields (register name, command string, `update_period`) are
declared without a boilerplate `__init__`.

- `AttrR(dt, io: ReadIO[DType_T] | None)`, `AttrW(dt, io: WriteIO[DType_T] |
  None)`, `AttrRW(dt, io: ReadWriteIO[DType_T] | None)`. Passing a
  read-only IO to an `AttrRW` is a **static** type error, not a runtime
  `_validate_io` check — the abstract methods force a subclass to implement
  the right surface for the `Attr` flavour it is attached to.
- `update_period` moves onto `ReadIO` — it describes the IO's polling
  behaviour, not a property of the attribute. `Controller.create_api_and_tasks`
  schedules from `attr.io.update_period` instead of pattern-matching on
  `AttributeIORef` (`control_system.py`/`controller.py`'s
  `case AttrR(_io_ref=AttributeIORef(update_period=update_period))` becomes a
  direct attribute access).
- **Delete:** `AttributeIORef`, the `ios=` constructor kwarg on
  `BaseController`/`Controller`/`ControllerVector`, `_validate_io`,
  `_connect_attribute_ios`, `_attribute_ref_io_map`, `__init_subclass__`'s
  generic-arg sniffing in `AttributeIO`, and the second TypeVar —
  `Attribute[DType_T, AttributeIORefT]` collapses to `Attribute[DType_T]`,
  making `AttrRW[float]` structurally isomorphic to ophyd-async's
  `SignalRW[float]`.
- `io=None` keeps today's soft-attribute behaviour: `AttrRW` self-wires
  setpoint→readback via `_internal_update`, and the sync-setpoint machinery
  is unaffected. This remains the analogue of ophyd-async's
  `soft_signal_rw`.
- The one-off "no subclass needed" case is served by the unified `attr`
  factory (see [ADR 18](0018-attr-decorator-sugar.md)) — `self.x =
  attr(getter=cb)` / `@attr` — **not** by separate `CallbackReadIO`/
  `CallbackWriteIO` classes. The same `attr` covers the read-only and
  read/write callback cases, so the two adapters are not shipped.

### Datatype metadata: the `*Meta` TypedDicts

`DataType` classes are gone (ADR 15/17). The metadata they carried (precision,
units, nested limits, …) moves to a per-datatype `TypedDict` — `FloatMeta`,
`IntMeta`, `StrMeta`, `BoolMeta`, `EnumMeta`, `Array1DMeta`, `TableMeta` — and
**the resolved metadata is stored on the `Attribute` itself** (`attr.meta`),
not on a separate datatype object. Every transport/connector that read
`attr.datatype.precision`/`.units`/`.limits`/`.choices` now reads `attr.meta`
(enum `choices` come from the python type; `EnumMeta` is display-only).

Two spellings, two validation layers:

- **Procedural (statically checked):** the `Attr*` constructors are overloaded
  per datatype so the right `*Meta` is unpacked into `**kwargs`:

  ```python
  # conceptually, one overload per datatype:
  def AttrRW(dtype: type[float], *, io=..., **kwargs: Unpack[FloatMeta]) -> AttrRW[float]: ...

  self.temperature = AttrRW(float, precision=3, units="deg", io=TempIO(...))
  # AttrRW(str, precision=3) is a static type error
  ```

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
`(child, extras)` mechanism.

The `*Meta` module location is deferred to the public-API-namespace decision
(#406); land it provisionally until then.

Migration is mechanical for the common case (an old `AttributeIO` subclass
absorbs its `AttributeIORef`'s fields into its own `__init__` and is
constructed once per attribute instead of once per controller):

```python
# Before (ADR 9 shape)
class TempIORef(AttributeIORef):
    name: str

class TempIO(AttributeIO[float, TempIORef]):
    async def update(self, attr: AttrR[float, TempIORef]) -> None:
        resp = await self._conn.send_query(f"{attr.io_ref.name}?\r\n")
        await attr.update(float(resp))

ramp_rate = AttrRW(Float(), io_ref=TempIORef(name="R"))
# ... elsewhere: Controller(ios=[TempIO(conn)])

# After
class TempIO(ReadWriteIO[float]):
    def __init__(self, conn: IPConnection, name: str, update_period=0.2):
        super().__init__(update_period=update_period)
        self._conn, self._name = conn, name

    async def update(self, attr: AttrR[float]) -> None:
        resp = await self._conn.send_query(f"{self._name}?\r\n")
        await attr.update(float(resp))

    async def send(self, attr: AttrW[float], value: float) -> None:
        await self._conn.send_command(f"{self._name}={value}\r\n")

self.ramp_rate = AttrRW(Float(), io=TempIO(conn, "R"))
```

`fastcs-catio`'s three-IO-per-controller pattern becomes three IO
*instances*, one per relevant attribute, with no registry needed at all.
`fastcs-secop`'s private `_call_sync_setpoint_callbacks` call is replaced by
a public method on `AttrW`/`ReadWriteIO` — exact shape is an open question.

## Consequences

- Every driver that declared `AttributeIORef` subclasses must migrate them
  into `AttributeIO.__init__` fields — see the affected §9 files in the
  sub-issues of #388 (`attributes/`, `controllers/base_controller.py`,
  `controllers/controller.py`) and the corresponding downstream repo issues.
- `Attribute` loses its second generic parameter, simplifying every type
  hint in downstream code (`AttrR[float, MyRef]` → `AttrR[float]`).
- Access-mode compatibility between an `Attr` and its `io=` argument is
  caught by the type checker instead of at runtime in `_validate_io` —
  earlier feedback for driver authors, at the cost of losing the runtime
  "no AttributeIO registered for this ref type" error message; a
  misconfigured `io=None` on an attribute that needed IO now simply behaves
  as a soft attribute rather than raising loudly. Whether this needs a
  runtime check as well (e.g. in `post_initialise`) is an open question.
- [ADR 12](0012-attribute-io-naming-convention.md)'s guidance (subclass to
  get a shorter driver-local name) still applies to the new `ReadIO`/
  `WriteIO`/`ReadWriteIO` names.

## Resolved in review (#402)

- **Runtime check: yes.** Alongside the static type error, a runtime check
  (e.g. at `post_initialise`) catches a read-only IO on a write-capable `Attr`
  for the dynamically-built `Any`-typed case (`fastcs-secop`,
  `fastcs-PandABlocks`).
- **`attr.io` becomes a public, typed property** — the sanctioned way to
  recover an attribute's IO-specific metadata from *outside* its `send`/
  `update` (replaces `fastcs-catio`'s `attribute.io_ref` access).
- **No `CallbackReadIO`/`CallbackWriteIO` in core.** The one-off callback case
  folds into the unified `attr` factory ([ADR 18](0018-attr-decorator-sugar.md));
  the same decorator/factory covers the read-only and read/write cases.
- **Setpoint echo is an attribute-cache guarantee, not a transport one
  (@Tom-Willemsen / @shihab-dls, #402):** `set()` caching `.setpoint` before it
  runs the setter fixes the *framework*-level report that a setpoint PV didn't
  reflect the just-written value, and is the sanctioned secop echo. Whether a
  *remote client* sees that value immediately is transport-dependent, and the two
  transports differ. **PVA** posts the setpoint as soon as it is written, then the
  record may later go into alarm if the setter rejects it. **CA** posts the PV
  update only *after* the update callback — where alarms are set — completes, so a
  long-running setter delays the CA-visible setpoint until the send returns. This
  means the `set()` semantics above are **not** a cross-transport "instantly
  visible" guarantee. Realigning CA to PVA's post-before-send ordering (so GUIs
  get immediate feedback on CA too) is a **transport-layer** follow-up, tracked
  separately from this attribute-IO rework and not gating it.

## Open questions (awaiting input)

Both original open questions are closed by the 2026-07-22 getter/setter model:

1. ~~Final IO class names (`ReadIO`/`WriteIO`/`ReadWriteIO` vs …)~~ — **moot**: the
   IO class hierarchy is gone; IO is plain `getter`/`setter` callables.
2. ~~Public replacement for `fastcs-secop`'s `_call_sync_setpoint_callbacks`~~ —
   **resolved**: a `setter` returning `T | Update[T]` *is* the sanctioned setpoint
   echo (updates readback + `AttrW` setpoint cache).
