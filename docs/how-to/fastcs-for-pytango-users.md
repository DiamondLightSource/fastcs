# FastCS for PyTango Users

If you write Tango Device Servers with PyTango, the shape of a FastCS controller
will already be familiar: a class, some attributes, some commands. This page
pairs the PyTango spelling with the FastCS one, so you can carry what you know
across.

The headline difference is that a FastCS controller is not tied to Tango. The
same class is served over Tango, EPICS (Channel Access or PV Access), REST and
GraphQL - see [](./multiple-transports.md).

## Hello world

PyTango's simplest attribute is one decorated getter:

```python
from tango.server import Device, attribute


class PowerSupply(Device):
    @attribute
    def voltage(self) -> float:
        return 2.5
```

FastCS says the same thing with `@attr`:

```python
from fastcs.attributes import attr
from fastcs.controllers import Controller


class PowerSupply(Controller):
    @attr
    async def voltage(self) -> float:
        return 2.5
```

Two differences to notice:

- The getter is `async`. FastCS controllers run on one event loop, so a getter
  that talks to a device awaits it rather than blocking every other attribute.
- The datatype comes from the return annotation. There is no `dtype=` keyword to
  keep in step with the code - `-> float` is one real annotation, checked by your
  type checker as well as by FastCS.

## Writing as well as reading

PyTango pairs a `voltage` attribute with a separately named `write_voltage`
method. FastCS does the same, with the pairing made by a decorator rather than
by the name:

```python
class PowerSupply(Controller):
    @attr(units="V", precision=3)
    async def voltage(self) -> float:
        """Output voltage."""
        return float(await self._conn.query("V?"))

    @voltage.setter
    async def set_voltage(self, value: float) -> None:
        await self._conn.send(f"V={value}")
```

A getter alone gives you a read-only `AttrR`; adding a `@voltage.setter`
method makes `voltage` an `AttrRW`. There is no write-only decorator - a
write-only attribute is rare enough to be written longhand as
`AttrW(setter=...)`.

The setter keeps a name of its own - `set_voltage` here, but it can be called
whatever reads best - so the two halves of one attribute are never two
declarations of one name. It also stays an ordinary method, so
`await self.set_voltage(2.5)` writes to the device directly, while
`await self.voltage.set(2.5)` writes through the attribute and updates what
clients see.

The getter's docstring becomes the attribute's description, and keyword
arguments to `@attr` are the attribute's metadata - `units`, `precision`,
`limits`, `group`, `description`. They are checked against the datatype the
getter returns, so `precision` on a `-> str` getter is an error rather than a
field that is silently ignored.

:::{note}
A type checker reads `voltage` as the read-only declaration the `@attr` line
makes, since nothing later in the class body can change the type of a name
already bound. So `self.voltage` is an `AttrR[float]` to your type checker even
where a setter has made it an `AttrRW[float]` at runtime, and a call to
`self.voltage.set(...)` needs narrowing:

```python
assert isinstance(self.voltage, AttrRW)
await self.voltage.set(2.5)
```

Writing through your own setter method - `await self.set_voltage(2.5)` - needs
nothing, and is usually what a controller wants anyway. Build the attribute in
`__init__` with `AttrRW(getter=..., setter=...)` if you would rather have the
narrower static type at every use site.
:::

## Deciding when a value is read

PyTango polls an attribute on a period configured per device, outside the code.
In FastCS the schedule is part of the declaration, and is the same
`Polled`/`NotPolled` vocabulary the procedural form uses:

```python
from fastcs.attributes import NotPolled, Polled, attr


class PowerSupply(Controller):
    @attr(Polled(period=0.5), units="V")
    async def voltage(self) -> float:
        """Read every half second, because the device changes it."""
        return float(await self._conn.query("V?"))

    @attr
    async def serial_number(self) -> str:
        """Read once, when the controller connects."""
        return await self._conn.query("*IDN?")

    @attr(NotPolled())
    async def last_error(self) -> str:
        """Never read on a schedule - only when something asks for it."""
        return await self._conn.query("ERR?")
```

A bare `@attr` means read once, at connect - the same default a bare
`getter=` has. See [](./update-attributes-from-device.md) for the whole picture,
including devices that push values at you rather than being polled.

## Commands

PyTango's `@command` and FastCS's `@command` line up directly, including typed
arguments and return values:

```python
from fastcs.methods import command


class PowerSupply(Controller):
    @command()
    async def reset(self) -> None:
        """Return the supply to its power-on state."""
        await self._conn.send("*RST")
```

See [](./typed-commands.md) for arguments and return values, and which
transports can serve them.

## When not to use `@attr`

`@attr` is the simple case: one attribute, one device call, known at the time
you write the class. It is sugar over the procedural form, and there are two
other spellings for when it stops fitting:

- **The attribute needs more than a getter and a setter** - a shared connection
  object, several attributes built in a loop, values that come from one
  request - build them in `__init__` with `AttrR(getter=...)` /
  `AttrRW(getter=..., setter=...)` directly. `@attr` degrades into exactly that
  form, so nothing is lost by moving.
- **The device describes itself** - the attributes are discovered by asking the
  device what it has, rather than written out. Declare what your code refers to
  as type hints and let the controller fill them in at initialisation. See
  [](../tutorials/dynamic-drivers.md).

There is no free-function `attr()` factory: outside a class body, write the
constructor.
