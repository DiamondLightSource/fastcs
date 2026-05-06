# Run Multiple Transports Simultaneously

This guide shows how to expose a fastcs driver through multiple protocols at once.

## Basic Setup

Pass a list of transports to `FastCS`:

```python
from fastcs.control_system import FastCS
from fastcs.transports import (
    EpicsCATransport,
    GraphQLTransport,
    RestTransport,
)

controller = MyController()
controller.set_id("DEVICE")  # PV prefix for EPICS / route prefix for REST

fastcs = FastCS(
    controller,
    [
        EpicsCATransport(),
        RestTransport(),
        GraphQLTransport(),
    ]
)
fastcs.run()
```

All transports run concurrently, exposing the same controller API.

## Choosing controller ids across transports

Each transport derives its addressing from the controller's id (the PV prefix
for EPICS, the URL prefix for REST, the top-level Query field for GraphQL),
and each enforces its own charset at startup.

| Transport | Allowed id charset |
|-----------|--------------------|
| EPICS CA  | `[A-Za-z0-9_-]+`, plus the 60-char PV name limit |
| EPICS PVA | `[A-Za-z0-9_-]+` |
| REST      | `[A-Za-z0-9_-]+` |
| GraphQL   | `[A-Za-z_][A-Za-z0-9_]*` (GraphQL `Name`: no hyphens, no leading digit) |

If you serve the same controller through multiple transports, use the
intersection — a leading letter or underscore followed by letters, digits and
underscores. GraphQL is the lowest common denominator: an id like `dev-01`
will start an EPICS or REST transport happily but fail fast when GraphQL is
added.

## Available Transports

| Transport | Protocol | Install Extra | Primary Use Case |
|-----------|----------|---------------|------------------|
| `EpicsCATransport` | EPICS Channel Access | `fastcs[epicsca]` | Control system integration |
| `EpicsPVATransport` | EPICS PV Access | `fastcs[epicspva]` | Modern EPICS with structured data |
| `TangoTransport` | Tango | `fastcs[tango]` | Tango control system |
| `RestTransport` | HTTP REST | `fastcs[rest]` | Web applications, debugging |
| `GraphQLTransport` | GraphQL | `fastcs[graphql]` | Flexible queries, web clients |

Install extras as needed:

```bash
pip install "fastcs[epicsca,rest,graphql]"
```

## Transport Configuration

Each transport has its own options:

### EPICS Channel Access

The PV prefix is the controller's id (set via `controller.set_id(...)` or
auto-set by `launch()` from the YAML key).

```python
from pathlib import Path
from fastcs.transports import (
    EpicsCATransport,
    EpicsDocsOptions,
    EpicsGUIOptions,
)

epics_ca = EpicsCATransport(
    gui=EpicsGUIOptions(
        output_dir=Path("./opis"),
        title="Device Control",
    ),
    docs=EpicsDocsOptions(
        output_dir=Path("./reference"),
    ),
)
```

### EPICS PV Access

```python
from fastcs.transports import EpicsPVATransport

epics_pva = EpicsPVATransport()
```

### REST

```python
from fastcs.transports import RestTransport
from fastcs.transports.rest import RestServerOptions

rest = RestTransport(
    rest=RestServerOptions(
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )
)
```

### GraphQL

```python
from fastcs.transports import GraphQLTransport
from fastcs.transports.graphql import GraphQLServerOptions

graphql = GraphQLTransport(
    graphql=GraphQLServerOptions(
        host="localhost",
        port=8081,
    )
)
```

### Tango

```python
from fastcs.transports import TangoTransport, TangoDSROptions

tango = TangoTransport(
    tango=TangoDSROptions(
        device_name="test/device/1",
    ),
)
```

## EPICS CA + PVA Together

Run both EPICS protocols simultaneously:

```python
from pathlib import Path

from fastcs.transports import (
    EpicsCATransport,
    EpicsGUIOptions,
    EpicsPVATransport,
)

controller.set_id("DEVICE")

fastcs = FastCS(
    controller,
    [
        EpicsCATransport(
            gui=EpicsGUIOptions(output_dir=Path("./opis")),
        ),
        EpicsPVATransport(),
    ]
)
```

Both transports derive the same PV prefix from the controller's id and
expose identical PVs.

## YAML Configuration

When using the `launch()` framework, configure transports in YAML.

See [Using the Launch Framework](launch-framework.md) for details.
