# Controllers

FastCS provides three controller classes: `Controller`, `ControllerVector`, and
`BaseController`. This document explains what each does and when to use each.

## Controller

`Controller` is the primary building block for FastCS drivers. It can serve two roles:

**Root controller:** passed to the `FastCS` launcher in a `ControllerSpec`. In this
role, FastCS opens the `Connection`s it is given, constructs it, calls its lifecycle
hooks and runs the scan tasks it creates on the event loop.

**Sub controller:** attached to a parent controller via `add_sub_controller()` or by
assigning it as an attribute. In this role, the sub controller's lifecycle hooks
(`reconnect`, `initialise`) are not called automatically by FastCS. The parent
controller is responsible for calling them as part of its own lifecycle, if required.

### Lifecycle hooks

| Method | Purpose |
|---|---|
| `initialise` | Dynamically add attributes on startup, before the API is built |
| `setup` | Run once at startup, before the initial reads. Called on every controller in the tree |
| `reconnect` | Re-open connection after scan error |

Connections to the device are not opened by the controller. They are `Connection`s
passed to its constructor, which FastCS opens before constructing it and closes on
shutdown.

### Scan task behaviour

When used as the root controller, FastCS collects all `@scan` methods and readable
attributes with `update_period` set, across the whole controller hierarchy to be run as
background tasks by FastCS. Scan tasks are gated on the `_connected` flag: if a scan
raises an exception, `_connected` is set to `False` and tasks pause until `reconnect`
sets it back to `True`.

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

    async def reconnect(self):
        try:
            await self._connection.close()
            await self._connection.connect()
            self._connected = True
        except Exception:
            logger.error("Failed to reconnect")

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
- The number of instances may vary (e.g. determined at runtime during `initialise`)

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
