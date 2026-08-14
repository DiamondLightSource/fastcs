# Transports

This guide explains how transports connect FastCS controllers to external protocols, and how they use attribute callbacks to keep the protocol layer synchronized with attribute values.

## Transport Architecture

A transport connects a `ControllerAPI` to an external protocol. The `ControllerAPI` provides read-only access to:

- Attributes (`AttrR`, `AttrW`, `AttrRW`)
- Command methods (`@command`)
- Scan methods (`@scan`)
- Sub-controller APIs (hierarchical structure)

## Implementing a Transport

Subclass `Transport` and implement `connect()` and `serve()`:

```python
import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastcs.controllers import ControllerAPI
from fastcs.transports.transport import Transport

@dataclass
class MyTransport(Transport):
    """Custom transport implementation."""

    host: str = "localhost"
    port: int = 9000

    def connect(
        self,
        controller_api: ControllerAPI,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Called during FastCS initialization.

        Store the controller_api and set up your protocol server.
        """
        self._controller_api = controller_api
        self._loop = loop
        self._server = MyProtocolServer(controller_api, self.host, self.port)

    async def serve(self) -> None:
        """Called to start serving.

        This runs as an async background task. It can block forever.
        """
        await self._server.start()

    @property
    def context(self) -> dict[str, Any]:
        """Optional: Add variables to the interactive shell."""
        return {"my_server": self._server}
```

## Working with ControllerAPI

The `ControllerAPI` provides access to the controller's attributes and methods. Use `walk_api()` to traverse the entire controller hierarchy and register all attributes and commands. Use pattern matching to handle different attribute types.

```python
for controller_api in root_controller_api.walk_api():
    for name, attribute in controller_api.attributes.items():
        match attribute:
            case AttrRW():
                protocol.create_read(name, attribute)
                protocol.create_write(name, attribute)
            case AttrR():
                protocol.create_read(name, attribute)
            case AttrW():
                protocol.create_write(name, attribute)

    for name, command in controller_api.command_methods.items():
        protocol.create_command(name, command)
```

## Attributes

Transports use attribute callbacks to keep their protocol-specific representations synchronized with attribute values:

---

<div style="text-align: center">

```{raw} html
:file: ../images/data-flow.excalidraw.svg
```

</div>

---

The diagram above shows the data flow between users, transports, attributes, and
hardware. The following table gives an overview of the data flow for the transport
layer.

| Callback | Registered with | Triggered By | Direction | Purpose |
|----------|-----------------|--------------|-----------|---------|
| Readback | `add_readback_callback()` | `attr.update(value)` | Publish ↑ | Update protocol representation when the attribute's readback changes |
| Setpoint | `add_setpoint_callback()` | `attr.set(value)` | Publish ↑ | Update protocol representation when the attribute's setpoint changes |
| Update Metadata | `add_update_meta_callback()` | `meta` property changes | Publish ↑ | Update protocol metadata when it changes |
| Set | `attr.set(value)` | Transport receives user input | Set ↓ | Forward write requests from protocol to attribute |

### Readback Callbacks

Use `add_readback_callback()` to update the protocol layer when an attribute's
readback changes.

```python
def create_read(name, attribute):
    protocol_read = Protocol(name)

    async def update_protocol_value(value):
        protocol_read.post(value)

    attribute.add_readback_callback(update_protocol_value)
```

The callback receives the new value and should update the protocol-specific
representation (e.g., posting to a PV, updating a REST endpoint cache, publishing the
change to a subscriber).

### Update Metadata Callbacks

Use `add_update_meta_callback()` to update protocol metadata when an attribute's
metadata changes. This is useful for protocols that expose that metadata (like EPICS
record fields).

```python
def create_read(name, attribute):
    ...

    attribute.add_readback_callback(update_protocol_value)

    def update_protocol_metadata(meta: Meta):
        protocol_read.set_units(meta.get("units"))
        limits = meta.get("limits")
        if limits is not None:
            protocol_read.set_limits(limits.control.low, limits.control.high)

    attribute.add_update_meta_callback(update_protocol_metadata)
```

The callback receives the new `Meta` and should update the protocol's metadata
representation (e.g., EPICS record fields like `EGU`, `HOPR`, `LOPR`). Every field is
optional, so read them with `.get()`.

### Setpoint Callbacks

Use `add_setpoint_callback()` to update the protocol layer when an attribute's
setpoint changes. A transport must **not** update its own setpoint display directly -
it registers a callback and lets the attribute drive it, so that every transport
agrees on the setpoint however it was changed (see
[](./decisions/0020-transport-setpoint-mirroring)).

```python
def create_write(name, attribute):
    protocol_setpoint = Protocol(name)

    async def update_protocol_setpoint(value):
        protocol_setpoint.post(value)

    async def handle_write(value):
        await attribute.set(value)

    attribute.add_setpoint_callback(update_protocol_setpoint)
```

The callback fires when:

- a write arrives through *any* transport - `set()` caches the requested value and
  publishes it before running the setter, so the display updates immediately rather
  than waiting for a slow device;
- the setter returns a value, which replaces it with the device's accepted or clamped
  value;
- a getter or setter returns `Update(readback=..., setpoint=...)`, for a device that
  reports its own setpoint;
- the first readback arrives on an `AttrRW` that has never been written. An `AttrRW`
  starts with no known setpoint, so this is what stops a setpoint display sitting at
  the datatype's default until someone writes to it. No seeding is required in the
  transport.

### Set

When the transport receives a write request from the protocol, call `await
attribute.set(value)` to forward it to the attribute. This triggers validation, caches
the value as the attribute's `.setpoint` (firing the setpoint callbacks above), and (if
the attribute has one) runs its `setter` to propagate the value to the device.

## Commands

Transports can trigger commands, which connect directly to method calls rather than stateful attributes.

```python
def create_command(name, command):
    protocol_command = Protocol(name)

    async def handle_command():
        await command.fn()
        protocol_command.post()
```

## Usage

Transports are automatically registered when subclassing `Transport`:

```python
from fastcs.transports import Transport

@dataclass
class MyTransport(Transport):
    # Automatically added to Transport.subclasses
    pass
```

This allows the transport to be used in YAML configuration files.
