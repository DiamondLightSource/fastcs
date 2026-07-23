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
| On Update | `add_on_update_callback()` | `attr.update(value)` | Publish ↑ | Update protocol representation when attribute value changes |
| Update Datatype | `add_update_datatype_callback()` | `datatype` property changes | Publish ↑ | Update protocol metadata when datatype changes |
| Set | `attr.set(value)` | Transport receives user input | Set ↓ | Forward write requests from protocol to attribute |

### On Update Callbacks

Use `add_on_update_callback()` to update the protocol layer when an attribute's value changes.

```python
def create_read(name, attribute):
    protocol_read = Protocol(name)

    async def update_protocol_value(value):
        protocol_read.post(value)

    attribute.add_on_update_callback(update_protocol_value)
```

The callback receives the new value and should update the protocol-specific
representation (e.g., posting to a PV, updating a REST endpoint cache, publishing the
change to a subscriber).

### Update Datatype Callbacks

Use `add_update_datatype_callback()` to update protocol metadata when an attribute's datatype changes. This is useful for protocols that expose datatype metadata (like EPICS record fields).

```python
def create_read(name, attribute):
    ...

    attribute.add_on_update_callback(update_protocol_value)

    def update_protocol_metadata(datatype: DataType):
        protocol_read.set_units(datatype.units)
        protocol_read.set_limits(datatype.min, datatype.max)

    attribute.add_update_datatype_callback(update_protocol_metadata)
```

The callback receives the new `DataType` instance and should update the protocol's metadata representation (e.g., EPICS record fields like `EGU`, `HOPR`, `LOPR`).

### Set

When the transport receives a write request from the protocol, call `await
attribute.set(value)` to forward it to the attribute. This triggers validation, caches
the value as the attribute's `.setpoint`, and (if the attribute has one) runs its
`setter` to propagate the value to the device. If the setter returns a non-`None` value,
that becomes the attribute's new `.setpoint`/readback - the device's accepted or
clamped value. The transport should also update its own setpoint display directly
rather than relying on a callback.

```python
def create_write(name, attribute):
    protocol_setpoint = Protocol(name)

    async def handle_write(value):
        protocol_setpoint.post(value)
        await attribute.set(value)
```

### Seeding a Setpoint Display from the Readback

An `AttrRW`'s setpoint starts out equal to its datatype's default, which may not match
the device's actual current value until the first poll happens. To avoid a write PV
briefly displaying a stale default, seed it once the first readback value is known,
using a one-shot `add_on_update_callback()` on the readback side:

```python
from fastcs.attributes import AttrR


def create_write(name, attribute):
    protocol_setpoint = Protocol(name)

    ...

    if isinstance(attribute, AttrR):
        seeded = False

        async def seed_setpoint_once(value):
            nonlocal seeded
            if not seeded:
                seeded = True
                protocol_setpoint.post(value)

        attribute.add_on_update_callback(seed_setpoint_once)
```

This only applies to `AttrRW` (readable and writable) - a pure `AttrW` has no readback
to seed a setpoint display from.

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
