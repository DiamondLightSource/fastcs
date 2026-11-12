# Controllers

FastCS provides three controller classes: `Controller`, `ControllerVector`, and
`BaseController`. This document explains what each does and when to use each.

## Controller

`Controller` is the primary building block for FastCS drivers. It can serve two roles:

**Root controller:** passed to the `FastCS` launcher in a `ControllerSpec`. In this
role, FastCS opens the `Connection`s it is given, constructs it, calls its lifecycle
hooks and runs the scan tasks it creates on the event loop.

**Sub controller:** attached to a parent controller via `add_sub_controller()` or by
assigning it as an attribute. Its attributes are exposed under a prefixed path.

### Lifecycle hooks

| Method | Purpose |
|---|---|
| `setup` | Called by the framework after all attributes are added, before serving the application. Called on every controller in the tree |

Connections to the device are not opened by the controller. They are `Connection`s
passed to its constructor, which FastCS opens before constructing it, reconnects if the
link goes down, and closes on shutdown. A controller stores the connection it uses, and
passes it on to any sub controllers that need it. See
[Connections](connections.md).

### Scan task behaviour

FastCS collects the `@scan` methods and readable attributes with `update_period` set
from every controller in the tree, and runs them as background tasks. A scan that
raises an exception is logged, and runs again on its next period.

Every controller has a read-only `connected` attribute. When the connection a controller
holds goes down, FastCS sets `connected` to `False` and pauses that controller's scans.
Once the connection is back, it sets `connected` to `True`, re-runs the controller's
`ONCE` scans and resumes the others. Controllers that hold no connection, or a different
one, carry on.

```python
from fastcs.controllers import Controller
from fastcs.attributes import AttrR, AttrRW
from fastcs.datatypes import Float, String
from fastcs.methods import scan


class TemperatureController(Controller):
    temperature = AttrR(Float(units="degC"))
    setpoint = AttrRW(Float(units="degC"))

    def __init__(self, connection: DeviceConnection):
        super().__init__()
        self._connection = connection

    @scan(period=1.0)
    async def update_temperature(self):
        value = await self._connection.get_temperature()
        await self.temperature.update(value)
```

### Using Controller as a sub controller

When a `Controller` is nested inside another, it organises the driver into logical
sections and its attributes are exposed under a prefixed path. If the sub
controller needs the connection, the parent passes it down:

```python
class ChannelController(Controller):
    value = AttrR(Float())

    def __init__(self, connection: DeviceConnection):
        super().__init__()
        self._connection = connection


class RootController(Controller):
    channel: ChannelController

    def __init__(self, connection: DeviceConnection):
        super().__init__()
        self.channel = ChannelController(connection)
```

## ControllerVector

`ControllerVector` is a convenience wrapper for a set of controllers of the same type,
distinguished by a non-contiguous integer index rather than a string name.

Children are accessed via `controller[<index>]` instead of `controller.<name>`. The type
parameter `Controller_T` makes iteration type-safe when all children are the same
concrete type: iterating yields `Controller_T` directly, with no `isinstance` checks
needed. Mixing different subtypes is not prevented at runtime, but doing so widens the
inferred type to the common base, losing the type-safety benefit.

```python
from fastcs.controllers import Controller, ControllerVector


class ChannelController(Controller):
    value = AttrR(Float())


class RootController(Controller):
    channels: ControllerVector[ChannelController]

    def __init__(self, num_channels: int):
        super().__init__()

        self.channels = ControllerVector(
            {i: ChannelController() for i in range(num_channels)}
        )

    async def update_all(self):
        for index, channel in self.channels.items():
            value = await self._client.get_channel(index)
            await channel.value.update(value)
```

Key properties of `ControllerVector`:

- Indexes are integers and do not need to be contiguous (e.g. `{1: ..., 3: ..., 7: ...}`)
- All children must be `Controller` instances of the same type
- Named sub controllers cannot be added to a `ControllerVector`
- Children are exposed to transports with their integer index as the path component

### When to use ControllerVector instead of Controller

Use `ControllerVector` when:

- The device has a set of identical channels, axes, or modules identified by number
- You need to iterate over sub controllers and perform the same action on each
- The number of instances may vary (e.g. determined from the connection's introspection)

Use a plain `Controller` with named sub controllers when the sub controllers are
distinct components with different types or roles.

## BaseController

`BaseController` is the common base class for both `Controller` and `ControllerVector`.
It handles the creation and validation of attributes, scan methods, command methods, and
sub controllers, including type hint introspection and IO connection.

`BaseController` is public for use in **type hints only**. It should not be subclassed
directly when implementing a device driver. Use `Controller` or `ControllerVector`
instead.

```python
from fastcs.controllers import BaseController


def configure_all(controller: BaseController) -> None:
    """Accept any controller type for generic operations."""
    for name, attr in controller.attributes.items():
        ...
```
