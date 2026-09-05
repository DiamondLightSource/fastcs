# Declaring Attributes: Class Body vs `__init__`

A `Controller`'s class body holds **declarations**; its `__init__` holds
**construction with data**. Which of the two an attribute belongs in follows
from one question: is everything about the attribute known when you write the
class?

## Construct it in `__init__` when you know everything

If you can write the attribute down in full — its datatype, its metadata, and
the IO that reads and writes it — construct it in `__init__` and assign it to
`self`:

```python
class PowerSupplyController(Controller):
    def __init__(self, protocol: PowerSupplyProtocol) -> None:
        super().__init__()

        self.voltage = AttrRW(
            float,
            getter=Polled(protocol.get_voltage, period=0.5),
            setter=protocol.set_voltage,
            units="V",
            precision=3,
        )
```

This is most drivers, most of the time. Because the attribute is built per
instance, it can close over per-instance state — which is what lets one
channel's index be baked into its own getter rather than dispatched on at IO
time.

An `Attribute` **may not** be assigned in the class body. One built there would
be a single object shared by every instance of the controller, so two devices
of the same model would write into each other; FastCS raises at construction
rather than let that happen, and names the attribute.

## Declare it as a hint when the data arrives later

Some drivers cannot write the attribute down in full, because what it needs
comes from the device — a detector that reports its own parameter tree — or
from a protocol library that turns one line of metadata into a getter and a
setter. Those declare a **type hint**, and `ControllerFiller` creates the
attribute from it:

```python
class OdinDetector(Controller):
    frames: AttrRW[int]

    async def initialise(self) -> None:
        for name, spec in await self._query_parameter_tree():
            self.filler.fill_attribute(
                name, getter=spec.getter, setter=spec.setter, **spec.meta
            )

        self.filler.check_filled("the Odin parameter tree")
```

The hint is not a promise to build something later. `self.frames` **exists as
soon as `__init__` returns** — as an `AttrRW[int]` with no IO yet — so the rest
of `__init__` can reference it, hand it to a sibling, or subscribe to it. That
rule is what makes `initialise` safe to run in parallel across controllers:
only `__init__` is serial, and by the time it ends every attribute anything
refers to is there.

`fill_attribute` provisions the attribute **in place**, so a reference taken
during `__init__` is the same object that ends up serving the device. It
validates as it goes: the metadata against the datatype the hint declared
(`precision` on a `str` raises, naming the field and the attribute), and, when
you pass `datatype=`, what the device reported against what you declared.

### A hint that cannot name its datatype

Occasionally the datatype itself is only knowable over the wire — an enum whose
members the device reports. Write the hint without a subscript:

```python
class EigerDetector(Controller):
    state: AttrR          # enum built from the device's `allowed_values`
```

FastCS cannot create that one, so it is a **promise** instead: introspection
must add it with `add_attribute`, and `check_filled` fails if nothing did. The
access mode is still checked — adding an `AttrW` where an `AttrR` was promised
raises.

### Extras: metadata a protocol layer defines

An `Annotated` hint carries anything else you put in it, and the filler hands
it back untouched:

```python
class Instrument(SCPIController):
    power: Annotated[AttrRW[float], SCPIParam("P", precision=3, units="W")]
```

Core FastCS defines **no** extras vocabulary. `SCPIParam` above belongs to
whatever protocol package you build on top: it reads the extras off each
declaration, builds the getter and setter its protocol implies, and fills the
attribute. This is how a protocol library gets a declarative spelling of its
own without FastCS knowing anything about it.

## Checking what was promised

`check_filled(source)` raises if anything the class body declared is missing,
listing it by name and naming where the data was supposed to come from. FastCS
calls it across the whole controller tree after `initialise`, so a driver that
forgets cannot serve a half-built API; call it yourself at the end of your own
`initialise` to get the better error message. An `| None` hint is not required.

## Summary

| You know | Write |
|---|---|
| Everything about the attribute | `self.x = AttrRW(...)` in `__init__` |
| Its type, but not its IO or metadata | `x: AttrRW[int]` and fill it in `initialise` |
| Its access mode only | `x: AttrR` and `add_attribute` it in `initialise` |
| Nothing until the device answers | No declaration; `add_attribute` in `initialise` |

See [ADR 0013](decisions/0013-declarative-procedural-split-and-controller-filler.md)
for why there is one declarative mechanism rather than two.
