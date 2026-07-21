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

> Datatype spelling: the `Float()` etc. in the examples below predate
> [ADR 17](0017-naming-pass.md); read them as the ADR-17 python-type + `*Meta`
> form (`AttrRW(float, precision=3, io=...)`), since the `DataType` family is
> removed.

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

## Open questions (awaiting input)

1. Final class names: `ReadIO`/`WriteIO`/`ReadWriteIO` vs. `AttrRIO`/`AttrWIO`/
   `AttrRWIO` vs. something else — see [ADR 17](0017-naming-pass.md).
   *(awaiting @shihab-dls)*
2. Public replacement for `fastcs-secop`'s `_call_sync_setpoint_callbacks`:
   an optional `sync_setpoint` argument on `WriteIO.send`, or a public method
   on `AttrW` an IO author can call from `send`? *(awaiting @shihab-dls /
   @Tom-Willemsen)*
