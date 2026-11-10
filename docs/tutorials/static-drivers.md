# Creating a FastCS Driver

## Demo Simulation

Within FastCS there is a tickit simulation of a temperature controller. Clone the FastCS
repository and open it in VS Code. The simulation can be run with the
`Temp Controller Sim` launch config by typing `Ctrl+P debug ` (note the trailing
whitespace), selecting the launch config and pressing enter. The simulation will then
sit and wait for commands to be sent. When it receives commands, it will log them to the
console to show what it is doing.

:::{note}
FastCS must be installed with the `demo` extra for the demo simulator to run. This can
be done by running `pip install 'fastcs[demo]'`.
:::

This tutorial will walk through the steps of writing a device driver to control this
simulation.

## FastCS Controllers

The core of a FastCS device driver is the `Controller`. This class is used to implement
control of a device and instances can be loaded into a FastCS application to expose its
functionality.

Create a `TemperatureController` class that inherits from `Controller`.

::::{admonition} Code 1
:class: dropdown, hint

:::{literalinclude} /snippets/static01.py
:::

::::

## FastCS Launcher

The entrypoint to a FastCS application is the `FastCS` class. This takes a `Controller`
and a list of transports to expose the API through and provides a `run` method to launch
the application. Create a `FastCS` instance, pass the `TemperatureController` to it
along with an empty list of transports (for now).

::::{admonition} Code 2
:class: dropdown, hint

:::{literalinclude} /snippets/static02.py
:emphasize-lines: 2,9,11-12
:::

::::

Now the application runs, but it still doesn't expose any API because the `Controller`
is empty.

## FastCS Attributes

The simulator has an API to get its ID. To expose this in the driver, an `Attribute` can
be added to the `Controller`. There are 3 types of `Attribute`: `AttrR`, `AttrW` and
`AttrRW`, representing the access mode of the API. The ID can be read, but it cannot be
written, so add an `AttrR`. An `Attribute` also needs a type. The ID from the simulator
is a string, so `String` should be used.

::::{admonition} Code 3
:class: dropdown, hint

:::{literalinclude} /snippets/static03.py
:emphasize-lines: 1,3,8
:::

::::

Now the controller has a property that will appear in the API, but there are no
transports being run on the event loop to expose that API. The controller can be
interacted with in the console, but note that it hasn't populated any values because it
doesn't have a connection.

::::{admonition} Interactive Shell
:class: dropdown, hint

:::
In [1]: controller.device_id

Out[1]: AttrR(String())

In [2]: controller.device_id.readback

Out[2]: ''
:::

::::

## FastCS Transports

FastCS supports multiple transports to expose the API of the loaded `Controller`. The
following transports are currently supported

- EPICS CA (using `pythonSoftIOC`)
- EPICS PVA (using `p4p`)
- Tango (using `pytango`)
- GraphQL (using `strawberry`)
- HTTP (using `fastapi`)

One or more of these can be loaded into the application and run in parallel. Add the
EPICS CA transport to the application by creating an `EPICSCATransport` instance and
passing it in.

::::{admonition} Code 4
:class: dropdown, hint

:::{literalinclude} /snippets/static04.py
:emphasize-lines: 5,6,13,14
:::

::::

There will now be a `DEMO:DeviceId` PV being served by the application. However, the
record is unset because the `Controller` is not yet querying the simulator for the
value.

```bash
❯ caget -S DEMO:DeviceId
DEMO:DeviceId
```

Now that the controller has a PV, it would be useful to open a UI. Add EPICS GUI
options to the transport options and generate a `demo.bob` file to use with Phoebus.

::::{admonition} Code 5
:class: dropdown, hint

:::{literalinclude} /snippets/static05.py
:emphasize-lines: 1,7,15-18,20
:::

::::

The `demo.bob` will have been created in the directory the application was run from.

## FastCS Device Connection

The `Attributes` of a FastCS `Controller` need some IO with the device in order to get
and set values. This is implemented with plain `getter`/`setter` callables passed to the
`Attribute` constructor, together with a connection. Generally each driver implements
its own getter/setter logic and connection, but there are some built in connection
options.

Update the controller to create an `IPConnection` to communicate with the simulator over
TCP and implement a `connect` method that establishes the connection. The `connect`
method is called by the FastCS application at the appropriate time during start up to
ensure the connection is established before it is used.

:::{note}
The simulator control connection is on port 25565.
:::

::::{admonition} Code 6
:class: dropdown, hint

:::{literalinclude} /snippets/static06.py
:emphasize-lines: 4,15-22,27-28
:::

::::

:::{warning}
The application will now fail to connect if the demo simulation is not running.
:::

The `Controller` has now established a connection with the simulator. This connection
can be used by a `getter` callable to query the device API and update the value in the
`device_id` attribute. Note that the `Attribute` now has to be created in `__init__`,
after the connection exists, rather than as a class body instance - a getter needs to
close over a live connection, which doesn't exist yet when the class body is evaluated.
Write a `_get_device_id` method that queries the device and returns its value, and pass
it to `device_id` as `getter`.

:::{note}
Passing the getter bare, as here, means it is called once at start up. Wrap it in
`Polled(getter, period=...)` to have the base class call it repeatedly instead.
:::

::::{admonition} Code 7
:class: dropdown, hint

:::{literalinclude} /snippets/static07.py
:emphasize-lines: 13-19,21-23
:::

::::

:::{note}
If a getter raises, it won't crash the application, but it prints the error to the
terminal. - `Update loop ... stopped:`
:::

Now the PV will be set by reading from the simulator and the IOC has one fully
functional PV.

```bash
❯ caget -S DEMO:DeviceId
DEMO:DeviceId SIMTCONT123
```

## Building Up The API

The simulator supports many other commands, for example it reports the total power
currently being drawn with the `P` command. This can be exposed by adding another
`AttrR` with a `Float` datatype, but so far the getter for `device_id` only knows how to
send the `ID` command. This new attribute could get its own bespoke getter, but the
query-building logic is similar enough between commands that it is worth factoring out.

Extract a small `TemperatureProtocol` class that knows how to send a query or a command
for a given parameter name, casting the response to the right python type. Each
attribute then gets a thin getter method that just names the parameter and delegates to
the protocol.

:::{note}
All responses from the `IPConnection` are strings. This is fine for the `ID` command
because the value is actually a string, but for `P` the value is a float, so
`TemperatureProtocol.send_query` needs to explicitly cast to the correct type. It takes
the target python type as an argument (e.g. `int`, `float`, `str`) and calls it as a
constructor to perform the cast.
:::

:::{admonition} Code 8
:class: dropdown, hint

:::{literalinclude} /snippets/static08.py
:emphasize-lines: 12,15-27,34,38-39,41-45
:::

::::

Now the IOC has two PVs being polled periodically. The new PV will be visible in the
Phoebus UI on refresh (right-click). `DEMO:Power` will read as `0` because the simulator
is not currently running a ramp. To do that the controller needs to be able to set
values on the device, as well as read them back. The ramp rate of the temperature can be
read with the `R` command and set with the `R=...` command. This means the protocol also
needs a way to send values to the device, which `send_command` already provides.

Add a new `AttrRW` with type `Float` to get and set the ramp rate, giving it both a
`getter` and a `setter`.

:::{note}
The set commands do not return a response, so the setter uses `send_command` instead of
`send_query`.
:::

::::{admonition} Code 9
:class: dropdown, hint

:::{literalinclude} /snippets/static09.py
:emphasize-lines: 4,40-45,53-57
:::

::::

Two new PVs will be created: one to set the ramp rate and one to read it back.

```bash
❯ caget DEMO:RampRate_RBV
DEMO:RampRate_RBV              2
❯ caput DEMO:RampRate 5
Old : DEMO:RampRate                  2
New : DEMO:RampRate                  5
❯ caget DEMO:RampRate_RBV
DEMO:RampRate_RBV              5
```

The changes will also be visible in the simulator terminal.

```
INFO:fastcs.demo.simulation.device:Set ramp rate to 5.0
```

This adds the first method to modify the device, but more are needed to be able to run a
temperature ramp. The simulator has multiple temperature control loops that can be
ramped independently. They each have a common set of commands that control them
individually, for example to `S01=...` to set the start point for ramp 1, `E02=...` to
set the end point for ramp 2.

Given that the device has `n` instances of a common interface, it makes sense to create
a class to encapsulate this control and then instantiate it for each ramp the simulator
has. This can be done with the use of sub controllers. Controllers can be arbitrarily
nested to match the structure of a device and this structure is then mirrored to the
transport layer for the visibility of the user.

Create a `TemperatureRampController` with two `AttrRW`s for the ramp start and end, give
`TemperatureProtocol` an optional suffix so an instance can be shared with the parent
`TemperatureController` while still addressing an individual ramp, and add an argument
to define how many ramps there are, which is used to register the correct number of ramp
controllers with the parent.

::::{admonition} Code 10
:class: dropdown, hint

:::{literalinclude} /snippets/static10.py
:emphasize-lines: 30-53,57,73-77
:::

::::

New PVs will be added (e.g. `DEMO:R1:Start`):
- `DEMO:R{1,2,3,4}:Start`
- `DEMO:R{1,2,3,4}:Start_RBV`
- `DEMO:R{1,2,3,4}:End`
- `DEMO:R{1,2,3,4}:End_RBV`

Four buttons will also be added to the Phoebus UI to open sub screens for each ramp.

This allows the controller to set the range of every temperature ramp. Again, the
simulator terminal will confirm that the changes are taking effect. The final commands
needed to run a temperature ramp are the `N01` and `N01=` commands, which are used to
enable (and disable) the ramping.

Add an `AttrRW` to the `TemperatureRampController`s with an `Enum` type, using a
`StrEnum` with states `Off` and `On`.

::::{admonition} Code 11
:class: dropdown, hint

:::{literalinclude} /snippets/static11.py
:emphasize-lines: 1,31-33,48-53,67-71
:::

::::

Now the temperature ramp can be run.

```bash
❯ caput DEMO:R1:Enabled On
Old : DEMO:R1:Enabled                Off
New : DEMO:R1:Enabled                On
❯ caget DEMO:Power
DEMO:Power                     56.84
❯ caput DEMO:R1:Enabled Off
Old : DEMO:R1:Enabled                On
New : DEMO:R1:Enabled                Off
❯ caget DEMO:Power
DEMO:Power                     0
```

In the simulator terminal the progress of the ramp can be seen as it happens.

```
INFO:fastcs.demo.simulation.device:Started ramp 0
INFO:fastcs.demo.simulation.device:Target Temperatures: 10.000, 0.000, 0.000, 0.000
INFO:fastcs.demo.simulation.device:Actual Temperatures: 9.572, 0.000, 0.000, 0.000
INFO:fastcs.demo.simulation.device:Target Temperatures: 10.200, 0.000, 0.000, 0.000
INFO:fastcs.demo.simulation.device:Actual Temperatures: 9.952, 0.000, 0.000, 0.000
INFO:fastcs.demo.simulation.device:Target Temperatures: 10.400, 0.000, 0.000, 0.000
...
INFO:fastcs.demo.simulation.device:Stopped ramp 0
```

The target and actual temperatures visible in the simulator terminal are also exposed in
the API with the `T01?` and `A01?` commands.

## FastCS Methods

The applied voltage for each ramp is also available with the `V?` command, but the value
is an array with each element corresponding to a ramp. Here it will be simplest to
manually fetch the array in the parent controller and pass each value into ramp
controller. This can be done with a `scan` method - these are called at a defined rate,
similar to how each attribute's getter is polled.

Add an `AttrR` for the voltage to the `TemperatureRampController`, but do not give it a
`getter` - it is a soft attribute, pushed to directly by the parent controller's scan
method instead. Then add a method to the `TemperatureController` with a `@scan`
decorator that gets the array of voltages and sets each ramp controller with its value.
Also add `AttrR`s for the target and actual temperature for each ramp as described
above.

::::{admonition} Code 12
:class: dropdown, hint

:::{literalinclude} /snippets/static12.py
:emphasize-lines: 11,56-58,78-82,123-129
:::

::::

Creating attributes is intended to be a simple API covering most use cases, but where
more flexibility is needed wrapped controller methods can be useful to avoid adding
complexity to a getter/setter to handle a small subset of attributes. It is also useful
for implementing higher level logic on top of the attributes that expose the API of a
device directly. For example, it would be useful to have a single button to stop all of
the ramps at the same time. This can be done with a `command` method. These are similar
to `scan` methods except that they create an API in transport layer in the same way an
attribute does.

Add a method with a `@command` decorator to set enabled to false in every ramp
controller by calling `set` on each `enabled` attribute.

::::{admonition} Code 13
:class: dropdown, hint

:::{literalinclude} /snippets/static13.py
:emphasize-lines: 1,132-137
:::

::::

The new `DEMO:CancelAll` PV can be set (the value doesn't matter) to stop all of the
ramps.

```
❯ caget DEMO:R1:Enabled_RBV
DEMO:R1:Enabled_RBV            On
❯ caput DEMO:DisableAll 1
Old : DEMO:DisableAll
New : DEMO:DisableAll
❯ caget DEMO:R1:Enabled_RBV
DEMO:R1:Enabled_RBV            Off
```

## Logging

FastCS has convenient logging support to provide status and metrics from the
application. To enable logging from the core framework call `configure_logging` with no
arguments (the default logging level is INFO). To log messages from a driver, import the
singleton `logger` directly.

Create a module-level logger to log status of the application start up, and use it
inside `TemperatureProtocol.send_command` to log the commands it sends.

::::{admonition} Code 14
:class: dropdown, hint

:::{literalinclude} /snippets/static14.py
:emphasize-lines: 12,28,145,150
:::

::::

Try setting a PV and check the console for the log message it prints.

```
[2026-01-01 11:26:41.065+0000 I] Sending attribute value      [fastcs] command=E01=70
```

A similar log message could be added for the getters, but this would be very verbose.
For this use case FastCS provides the `Tracer` class, which can be inherited by anything
that wants to support selective, per-instance logging - `Attribute` and `BaseController`
already do. This enables the logging of `TRACE` level log messages that are disabled by
default, but can be enabled at runtime.

Make `TemperatureProtocol` inherit `Tracer` too, and update `send_query` to take a
`topic` argument and log a message showing the query that was sent and the response
from the device via `self.log_event`, passing through the attribute doing the query as
the `topic`. Update each getter to pass its own attribute as `topic`. Update the
`configure_logging` call to pass `LogLevel.TRACE` as the log level, so that when tracing
is enabled the messages are visible.

::::{admonition} Code 15
:class: dropdown, hint

:::{literalinclude} /snippets/static15.py
:emphasize-lines: 12,14,21,34-36,41,125,153
:::

::::

Enable tracing on the `power` attribute by calling `enable_tracing` and then enable a
ramp so that the value updates. Check the console to see the messages. Call
`disable_tracing` to disable the log messages for `power`.

```
In [1]: controller.power.enable_tracing()
[2026-01-01 11:11:12.060+0000 T] Query for attribute          [fastcs] query=P?, response=0.0
[2026-01-01 11:11:12.194+0000 I] PV put: DEMO:R1:Enabled = 1  [fastcs.transports.epics.ca.ioc] pv=DEMO:R1:Enabled, value=1
[2026-01-01 11:11:12.195+0000 I] Sending attribute value      [fastcs] command=N01=1
[2026-01-01 11:11:12.262+0000 T] Query for attribute          [fastcs] query=P?, response=29.040181873093132
[2026-01-01 11:11:12.463+0000 T] Query for attribute          [fastcs] query=P?, response=30.452524641833854
In [2]: controller.power.disable_tracing()
```

Only messages with `power` as their topic appear, even though every attribute's getter
is querying the device on the same period - other attributes' queries stay silent until
tracing is enabled on them too.

:::{note}
The `Tracer` can also be used as a module-level instance for use in free functions.

```python
from fastcs.tracer import Tracer

tracer = Tracer()

def handle_attribute(attr):
    tracer.log_event("Handling attribute", topic=attr)
```

These messages can then be enabled by calling `enable_tracing` on the module-level
`Tracer`, or more likely on a specific attribute.
:::

## Summary

This demonstrates some of the simple use cases for a statically defined FastCS driver.
It is also possible to instantiate a driver dynamically by instantiating a device during
startup. See the next tutorial for how to do this.
