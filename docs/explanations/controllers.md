# Controllers

FastCS provides three controller classes: `Controller`, `ControllerVector`, and
`BaseController`. This document explains what each does and when to use each.

## Controller

`Controller` is the primary building block for FastCS drivers. It can serve two roles:

**Root controller:** passed directly to the `FastCS` launcher. In this role, FastCS
will call its lifecycle hooks and run the scan tasks it creates on the event loop.

**Sub controller:** attached to a parent controller via `add_sub_controller()` or by
assigning it as an attribute. In this role, the sub controller's lifecycle hooks
(`connect`, `reconnect`, `initialise`, `disconnect`) are not called automatically by
FastCS. The parent controller is responsible for calling them as part of its own
lifecycle, if required.

### Lifecycle hooks

| Method | Purpose |
|---|---|
| `initialise` | Fill declared attributes, and add dynamic ones, before the API is built |
| `connect` | Open connection to device |
| `reconnect` | Re-open connection after scan error |
| `disconnect` | Release device resources before shutdown |

Attributes are constructed in `__init__`, or declared as class-body type hints
and created for you - see [](declaring-attributes.md) for which to use when.
An `Attribute` assigned in the class body is rejected: one object would be
shared by every instance of the controller.

### Scan task behaviour

When used as the root controller, FastCS collects all `@scan` methods and readable
attributes whose `getter` is wrapped in `Polled`, across the whole controller
hierarchy, to be run as background tasks by FastCS. Scan tasks are gated on the
`_connected` flag: if a scan
raises an exception, `_connected` is set to `False` and tasks pause until `reconnect`
sets it back to `True`.

```python
from fastcs.controllers import Controller
from fastcs.attributes import AttrR, AttrRW
from fastcs.methods import scan


class TemperatureController(Controller):
    def __init__(self, host, port):
        super().__init__()
        self._host, self._port = host, port

        self.temperature = AttrR(float, units="degC")
        self.setpoint = AttrRW(float, units="degC")

    async def connect(self):
        self._client = await DeviceClient.connect(self._host, self._port)
        self._connected = True

    async def reconnect(self):
        try:
            self._client = await DeviceClient.connect(self._host, self._port)
            self._connected = True
        except Exception:
            logger.error("Failed to reconnect")

    async def disconnect(self):
        await self._client.close()

    @scan(period=1.0)
    async def update_temperature(self):
        value = await self._client.get_temperature()
        await self.temperature.update(value)
```

### Using Controller as a sub controller

When a `Controller` is nested inside another, it organises the driver into logical
sections and its attributes are exposed under a prefixed path. If the sub
controller also has connection logic, the parent must invoke it explicitly:

```python
class ChannelController(Controller):
    def __init__(self):
        super().__init__()
        self.value = AttrR(float)

    async def connect(self):
        ...
        self._connected = True


class RootController(Controller):
    channel: ChannelController

    def __init__(self):
        super().__init__()
        self.channel = ChannelController()

    async def connect(self):
        await self.channel.connect()
        self._connected = True
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
    def __init__(self):
        super().__init__()
        self.value = AttrR(float)


class RootController(Controller):
    channels: ControllerVector[ChannelController]

    def __init__(self, num_channels: int):
        super().__init__()

        self.channels = ControllerVector(
            {i: ChannelController() for i in range(num_channels)}
        )

    async def connect(self):
        for channel in self.channels.values():
            await channel.connect()

        self._connected = True

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
sub controllers, including type hint introspection.

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
