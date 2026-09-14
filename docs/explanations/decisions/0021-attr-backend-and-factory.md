# 21. AttrBackend and AttrFactory

Date: 2026-09-10

**Related:** [ADR 9](0009-handler-to-attribute-io-pattern.md),
[ADR 13](0013-declarative-procedural-split-and-controller-filler.md),
[ADR 14](0014-attribute-io-rw-rework.md)

## Status

Accepted

## Context

`SCPIController` ([ADR 13](0013-declarative-procedural-split-and-controller-filler.md)'s
worked example of a filler-based protocol layer) hand-built a getter/setter closure
per declared attribute, each one closing over the attribute's command token and,
for a getter, the datatype that parses the device's text answer:

```python
self.filler.fill_attribute(
    declaration.name,
    getter=Polled(self._getter(param.param, datatype), period=self.poll_period),
    setter=setter,
    **param.meta,
)
```

Every controller that binds many attributes to one wire protocol re-implements this
same shape - a closure factory, `Polled` wrapping, and an `isinstance` check to skip
building a setter for a read-only attribute - with nothing shared between them. There
was no protocol-agnostic concept of "a thing that knows how to get/set a value given
some protocol-specific arguments" for `ControllerFiller` to build on.

## Decision

Introduce `AttrBackend` (`fastcs/attributes/backend.py`), a `Protocol` describing
exactly that:

```python
class AttrBackend(Protocol[*Ts, DType_T]):
    async def get(self, *args: *Ts) -> DType_T: ...
    async def set(self, value: DType_T, *args: *Ts) -> None: ...
```

and `AttrFactory` (`fastcs/attributes/factory.py`), built from one, with two halves:

- **Construction** - `attr_r`/`attr_w`/`attr_rw` build a *new* attribute with the
  backend's IO bound, for a controller that discovers attributes at runtime (no
  `Declaration` to fill).
- **Filling** - `fill(attr, *args, polled=...)` binds IO onto an attribute a
  `ControllerFiller` already created from a class-body hint, dispatching on the
  attribute's actual kind (`AttrR`/`AttrW`/`AttrRW`) so callers don't branch
  themselves.

`ControllerFiller.fill_from_backend(declaration, backend, *args, polled=..., **meta)`
is the entry point a protocol layer like `SCPIController` actually calls: it looks up
`declaration.child`, delegates IO-binding to `AttrFactory.fill`, and applies `**meta`
through the existing `fill_attribute` (unchanged - `fill_attribute` already treats a
missing `getter`/`setter` as "nothing to bind here", so no new code path was needed
in it for the meta-only call `fill_from_backend` makes).

**This is not `AttributeIO`/`AttributeIORef` (ADR 9) come back.** ADR 14 deleted that
pattern because its `AttributeIORef` existed solely to carry inert per-attribute data
until a class-scope `Attribute` instance's `AttributeIO` could be found *by type* at
`post_initialise()` - a problem [ADR 13](0013-declarative-procedural-split-and-controller-filler.md)
had already made obsolete by moving every attribute's construction into `__init__`/
`initialise()`, where a live connection is already in scope. `AttrBackend` has no ref
object, no type-based dispatch registry, and no controller-wide "list of IOs to
connect": a controller constructs its backend and hands it directly to
`AttrFactory`/`fill_from_backend` itself, the same way it would hand a bare getter/
setter to `fill_attribute` today. `SCPIController` now builds one `SCPIBackend` per
instance (mnemonic + parser as call arguments, not per-attribute construction state)
instead of one closure pair per attribute.


## Consequences

- `SCPIController` migrates from two hand-built closure factories (`_getter`/
  `_setter`) to one `SCPIBackend` instance and a single `fill_from_backend` call;
  behaviour is unchanged (every existing SCPI demo test passes unmodified).
- A future protocol layer (REST, say) gets the same shared mechanism for free:
  implement `get`/`set`, hand the backend to `fill_from_backend`.
- `fill_attribute`'s existing raw-getter/setter path is untouched and still the right
  choice for a controller that does not want a shared backend abstraction.
