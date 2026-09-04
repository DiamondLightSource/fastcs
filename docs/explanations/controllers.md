# Controllers

FastCS provides three controller classes: `Controller`, `ControllerVector`, and
`BaseController`. This document explains what each does and when to use each.

## Controller

`Controller` is the primary building block for FastCS drivers. It can serve two roles:

**Root controller:** passed directly to the `FastCS` launcher.

**Sub controller:** attached to a parent controller via `add_sub_controller()` or by
assigning it as an attribute.

The `ControllerRunner` owns the order of the startup sequence and calls the
lifecycle hooks of **every** controller in the tree, root and sub alike. A parent
never drives a child's lifecycle to compensate for sequencing.

### Lifecycle hooks

| Method | Purpose |
|---|---|
| `__init__` | Everything knowable without the device: settings, static attributes |
| `build` | Structure that depends on the device - attributes and sub controllers |
| `setup` | Hardware writes and checks, once the whole tree is built |

The same question, three ways:

| What do I need to answer this? | Where it goes |
|---|---|
| Nothing - settings and the class | `__init__` |
| The device | `build` |
| My children, connected | `setup` |

There is no `connect`, `reconnect` or `disconnect` hook. Opening the link, reopening
it after a failure and closing it at shutdown belong to the `Connection` and the
runner - see [connections](./connections.md).

`build` optionally receives whatever its connection's `connect` returned: write
`build(self)` for nothing, or `build(self, info)` to be handed the connection's
introspection result.

### Scan task behaviour

FastCS collects all `@scan` methods and readable attributes whose `getter` is wrapped
in `Polled`, across the whole controller hierarchy, to be run as background tasks.
Scan tasks are gated on the controller's **connection**: while that connection is
down they wait for it to come back rather than polling a link that cannot answer. A
controller with no connection is never gated.

A scan that raises is logged and retried. It does not mark the connection down -
only the connection's own IO can tell a dead transport from a device complaint.

```python
from fastcs.controllers import Controller
from fastcs.attributes import AttrR, AttrRW
from fastcs.methods import scan


class TemperatureController(Controller):
    connection: DeviceConnection

    temperature = AttrR(float, units="degC")
    setpoint = AttrRW(float, units="degC")

    def __init__(self, connections: Connections):
        self.connection = connections.get("device", DeviceConnection)
        super().__init__()

    @scan(period=1.0)
    async def update_temperature(self):
        value = await self._client.get_temperature()
        await self.temperature.update(value)
```

### Using Controller as a sub controller

When a `Controller` is nested inside another, it organises the driver into logical
sections and its attributes are exposed under a prefixed path. A sub controller that
talks to the same device holds the *same* connection object as its parent rather
than consulting it, so the two share one health state and one reconnect task:

```python
class ChannelController(Controller):
    connection: DeviceConnection

    value = AttrR(float)

    def __init__(self, connection: DeviceConnection):
        self.connection = connection
        super().__init__()


class RootController(Controller):
    connection: DeviceConnection

    channel: ChannelController

    def __init__(self, connections: Connections):
        self.connection = connections.get("device", DeviceConnection)
        super().__init__()
        self.channel = ChannelController(self.connection)
```

A sub controller that talks to a *different* device claims its own connection by
name from the registry instead. Nothing is inferred from tree position.

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
    value = AttrR(float)


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
- The number of instances may vary (e.g. determined at runtime during `build`)

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
