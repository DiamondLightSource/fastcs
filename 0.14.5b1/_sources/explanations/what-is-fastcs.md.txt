# What is FastCS?

FastCS is a Python framework for building device drivers for scientific instruments.
It separates the logic of communicating with a device from the control system used to
expose it, so the same driver works with EPICS (CA or PVA), Tango, REST, and GraphQL
without modification.

---

<div style="text-align: center">

```{raw} html
:file: ../images/overview.excalidraw.svg
```

</div>

---

## Architecture

A FastCS application has three layers:

**Controller** - a Python class that models the device. It holds attributes and
commands, implements connection logic, and creates periodic polling tasks. The
controller can create `AttributeIO`s to handle `update` and `send` operations between
attributes and the device.

**Attributes and commands** - typed values (`AttrR`, `AttrW`, `AttrRW`) and callable
actions (`@command`) declared on the controller. Attributes represent the device's
readable and writable parameters and carry metadata such as units, limits, and alarm
thresholds. Commands are actions that can be triggered to run.

**Transport** - the protocol layer that exposes the controller's attributes and commands
to a control system. FastCS provides transports for EPICS CA, EPICS PVA, Tango, REST,
and GraphQL.

---

<div style="text-align: center">

```{raw} html
:file: ../images/data-flow.excalidraw.svg
```

</div>

---

Sub-controllers let you model hierarchical devices — a sub-controller's attributes are
exposed under a prefixed path, and a set of identical sub-controllers can be grouped
using `ControllerVector`. See [Controllers](controllers.md) for details.

## Writing a driver

To create a driver, subclass `Controller`, declare attributes and commands as typed
class members, and implement lifecycle hooks (`connect`, `reconnect`, `disconnect`) and
periodic scan tasks. The [tutorials](../tutorials.md) walk through a complete example.

## Deploying as an application

Passing the controller class to `launch()` generates a standard CLI with `run` and
`schema` commands. The `run` command takes a YAML configuration file that specifies
both the controller's settings and which transports to start. Swapping or adding
transports requires only a config change, not a code change. See
[Launching the framework](../how-to/launch-framework.md) for details.

## Benefits

**Simple API** — a driver is a plain Python class with typed class-variable attributes.
There is no boilerplate or protocol-specific code in the driver itself.

**Control-system-agnostic testing** — controllers have no dependency on EPICS, Tango,
or any other control system and can be unit-tested as ordinary Python objects. Testing
device logic does not require a running IOC or device server.

**Multiple transports from a single driver** — the same controller can serve EPICS CA,
EPICS PVA, Tango, REST, and GraphQL simultaneously or in any combination.

**Automatic reconnection** — if a scan task raises an exception, FastCS pauses all
tasks and calls `reconnect()` automatically, resuming once the connection is restored.

**Auto-generated OPI screens** — EPICS transports generate CSS-Phoebus screen files
from the controller's attribute metadata, with no additional effort from the driver
author.

**Interactive shell** — FastCS starts an IPython shell alongside the running driver,
giving direct access to the live controller instance for inspection and ad-hoc commands
without restarting.

**Structured logging and per-attribute tracing** — FastCS uses
[loguru](https://loguru.readthedocs.io/) for structured logging. The `Tracer` mixin
adds TRACE-level logging to individual attributes, allowing fine-grained visibility
into specific values without increasing the verbosity of the whole driver.

**Graylog integration** — the generated CLI accepts a `--graylog-endpoint` option that
forwards all log output to a Graylog instance, with support for static and
environment-variable-sourced fields for log enrichment.
