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

Create a YAML configuration file matching the schema:

```yaml
# device_config.yaml
controller:
  ip_address: "192.168.1.100"
  port: 25565
  timeout: 10.0

transport:
  - epicsca:
      pv_prefix: "DEVICE"
```

Run with:

```bash
python my_driver.py run device_config.yaml
```

## Schema Generation

Generate JSON schema for the configuration yaml:

```bash
python my_driver.py schema > schema.json
```

Use this schema for IDE autocompletion in YAML files:

```yaml
# yaml-language-server: $schema=schema.json
controller:
  ip_address: "192.168.1.100"
  # ... IDE will provide autocompletion
```

## Transport Configuration

Transports are configured in the `transport` section as a list:

```yaml
transport:
  # EPICS Channel Access
  - epicsca:
      pv_prefix: "DEVICE"
    gui:
      output_path: "opis/device.bob"
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
python my_driver.py run config.yaml --log-level debug

# Send logs to Graylog
python my_driver.py run config.yaml \
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
