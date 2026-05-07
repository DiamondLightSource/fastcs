# Use the Launch Framework for CLI Applications

This guide shows how to use `launch()` to create deployable FastCS drivers with
automatic CLI generation and YAML configuration.

## Basic Setup

The `launch()` function generates a CLI from the controller's type hints:

```python
from fastcs.controllers import Controller
from fastcs.launch import launch

class MyController(Controller):
    pass

if __name__ == "__main__":
    launch(MyController)
```

This creates a CLI with:

- `--version` - Display version information
- `schema` - Output JSON schema for configuration
- `run <config.yaml>` - Start the controller with a YAML config file

## Adding Configuration Options

It is recommended to use a dataclass or Pydantic model for the controller's
configuration, as these provide schema generation and IDE support. The `launch()`
function checks that `__init__` has at most one argument (besides `self`) and that the
argument has a type hint, which is required to infer the schema:

```python
from dataclasses import dataclass

from fastcs.controllers import Controller
from fastcs.launch import launch

@dataclass
class DeviceSettings:
    ip_address: str
    port: int = 25565
    timeout: float = 5.0

class DeviceController(Controller):
    def __init__(self, settings: DeviceSettings):
        super().__init__()
        self.settings = settings

if __name__ == "__main__":
    launch(DeviceController, version="1.0.0")
```

## YAML Configuration Files

Create a YAML configuration file matching the schema. The conventional
filename is `fastcs.yaml`, but any filename works — `run` takes the path
as an argument:

```yaml
# fastcs.yaml
controllers:
  - id: DEVICE
    type: DeviceController
    ip_address: "192.168.1.100"
    port: 25565
    timeout: 10.0

transport:
  - epicsca: {}
```

Every entry carries a required `id:` plus a required `type:` discriminator
that names the Controller class to instantiate. The remaining fields under
each entry come straight from that class's `__init__` options type
(`DeviceSettings` here).

The `id:` (here `DEVICE`) is used verbatim as the EPICS PV prefix and as
the REST route prefix.

Run with:

```bash
python my_driver.py run fastcs.yaml
```

### Hosting multiple controllers

`controllers:` is a list, so a single application can host more than one
controller. Each entry needs a unique `id:`; the `type:` discriminator
selects which class to instantiate. For example, two `DeviceController`
instances on different IPs sharing a single transport list:

```yaml
# fastcs.yaml
controllers:
  - id: MAIN
    type: DeviceController
    ip_address: "192.168.1.100"
    port: 25565
    timeout: 10.0
  - id: AUX
    type: DeviceController
    ip_address: "192.168.1.101"
    port: 25565
    timeout: 10.0

transport:
  - epicsca: {}
```

When more than one class is registered with `launch([ClassA, ClassB])`,
each entry's `type:` selects between them.

For a real working example, see `src/fastcs/demo/fastcs.yaml`, which
hosts two `TemperatureController` instances and can be run with
`python -m fastcs.demo run src/fastcs/demo/fastcs.yaml`.

The transport list is shared across all controllers: each transport sees
the full set, and uses the per-entry id as the addressing prefix
(EPICS PV prefix, REST route prefix, GraphQL top-level Query field, Tango
device name segment).

## Schema Generation

Generate JSON schema for the configuration yaml:

```bash
python my_driver.py schema > schema.json
```

Use this schema for IDE autocompletion in YAML files:

```yaml
# yaml-language-server: $schema=schema.json
controllers:
  - id: DEVICE
    type: DeviceController
    ip_address: "192.168.1.100"
    # ... IDE will provide autocompletion
```

## Transport Configuration

Transports are configured in the `transport` section as a list:

```yaml
transport:
  # EPICS Channel Access
  - epicsca: {}
    gui:
      output_dir: "opis"
      title: "Device Control"

  # REST API
  - rest:
      host: "0.0.0.0"
      port: 8080

  # GraphQL
  - graphql:
      host: "localhost"
      port: 8081
```

## Logging Options

The `run` command includes logging options:

```bash
# Set log level
python my_driver.py run fastcs.yaml --log-level debug

# Send logs to Graylog
python my_driver.py run fastcs.yaml \
  --graylog-endpoint "graylog.example.com:12201" \
  --graylog-static-fields "app=my_driver,env=prod"
```

Available log levels: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

## Version Information

Pass a version string to display the driver version:

```python
launch(DeviceController, version="1.2.3")
```

```bash
$ python my_driver.py --version
DeviceController: 1.2.3
FastCS: 0.12.0
```

## Constraints

The `launch()` function requires:

1. Controller `__init__` must have at most 2 arguments (including `self`)
2. If a configuration argument exists, it must have a type hint

Using a dataclass or Pydantic model is recommended for the configuration type, as it enables JSON schema generation. Other type-hinted types will work, but will not produce a useful schema.

```python
# Valid - no config
class SimpleController(Controller):
    def __init__(self):
        super().__init__()

# Valid - with config
class ConfiguredController(Controller):
    def __init__(self, settings: MySettings):
        super().__init__()

# Invalid - missing type hint
class BadController(Controller):
    def __init__(self, settings):  # Error: no type hint
        super().__init__()

# Invalid - too many arguments
class TooManyArgs(Controller):
    def __init__(self, settings: MySettings, extra: str):  # Error
        super().__init__()
```
