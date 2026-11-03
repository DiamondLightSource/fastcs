# Arrange EPICS Screens with Groups and Sub-Controllers

This guide shows how to use `group` on attributes and commands to organise widgets into
labelled boxes on a screen, and how splitting a device into sub-controllers creates
navigable sub-screens for larger devices.

Both the CA and PVA EPICS transports generate screens from the same controller structure,
so the techniques shown here apply to both.

## Group Attributes and Commands into Boxes

By default, all attributes and commands on a controller appear as a flat list of widgets
on the generated screen. Assigning a `group` string places them together inside a labelled
box.

```python
from fastcs.attributes import AttrR, AttrRW
from fastcs.controllers import Controller
from fastcs.datatypes import Float, Int
from fastcs.methods import command


class PowerSupplyController(Controller):
    voltage = AttrRW(Float(), group="Output")
    current = AttrRW(Float(), group="Output")
    power = AttrR(Float(), group="Output")

    temperature = AttrR(Float(), group="Status")
    fault_code = AttrR(Int(), group="Status")

    @command(group="Actions")
    async def reset_faults(self) -> None:
        ...

    @command(group="Actions")
    async def enable_output(self) -> None:
        ...
```

The generated screen will show three boxes — **Output**, **Status**, and **Actions** —
each containing only the widgets assigned to that group. Attributes and commands with no
`group` are placed outside any box, directly on the screen.

## Use Sub-Controllers to Create Sub-Screens

For devices with many attributes, a single flat screen becomes unwieldy. Splitting
functionality across multiple controllers, connected with `add_sub_controller()`, causes
the transport to generate a top-level screen with navigation links to per-sub-controller
sub-screens.

```python
from fastcs.attributes import AttrR, AttrRW
from fastcs.controllers import Controller
from fastcs.datatypes import Float, Int
from fastcs.methods import command


class ChannelController(Controller):
    voltage = AttrRW(Float(), group="Output")
    current = AttrRW(Float(), group="Output")
    temperature = AttrR(Float(), group="Status")

    @command(group="Actions")
    async def enable(self) -> None:
        ...


class MultiChannelPSU(Controller):
    total_power = AttrR(Float())

    @command()
    async def disable_all(self) -> None:
        ...

    def __init__(self, num_channels: int) -> None:
        super().__init__()
        for i in range(1, num_channels + 1):
            self.add_sub_controller(f"Ch{i:02d}", ChannelController())
```

The top-level screen for `MultiChannelPSU` shows `TotalPower` and `DisableAll` alongside
buttons labelled **Ch01**, **Ch02**, … that each open the sub-screen for that channel.
Each channel sub-screen then shows the **Output**, **Status**, and **Actions** boxes
defined on `ChannelController`.
