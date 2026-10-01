# 7. Merge TransportAdapter and TransportOptions into Transport

Date: 2025-09-29

**Related:** [PR #220](https://github.com/DiamondLightSource/FastCS/pull/220)

## Status

Accepted

## Context

FastCS transports are implemented using two separate classes: `TransportAdapter` for implementation and separate `*Options` classes for configuration. This pattern requires:

- Two classes per transport
- Pattern matching logic in FastCS to create the right adapter from options
- Inconsistent constructor signatures across transports
- Redundant Options classes that only carry configuration data

## Decision

Merge `TransportAdapter` and `*Options` classes into a single `Transport` dataclass that combines configuration and implementation.

All transports should follow a unified pattern: configuration fields are dataclass attributes, and `connect()` and `serve()` methods handle initialization and execution. FastCS accepts Transport instances directly.

Key architectural changes:
- All transports use `@dataclass` decorator combining configuration and implementation
- Standardized `connect(controller_api, loop)` method to load controller API into transport
- Standardized `async serve()` method for running the transport service

## Consequences

### Benefits

- **Reduced API Surface:** 5 classes instead of 10 (one Transport per protocol)
- **Simpler Mental Model:** Configuration and implementation in one place
- **Consistent Interface:** All transports follow same initialization pattern

### Migration Pattern

**Before (Options + Adapter pattern):**
```python
# Configuration separate from implementation
@dataclass
class MyOptions:
    param1: str
    param2: int = 42

class MyTransport(TransportAdapter):
    def __init__(self, controller_api: ControllerAPI, options: MyOptions):
        self._options = options
        # Setup using self._options.param1
```

**After (Unified Transport):**
```python
# Configuration and implementation unified
@dataclass
class MyTransport(Transport):
    param1: str
    param2: int = 42

    def connect(self, controller_api: ControllerAPI, loop: asyncio.AbstractEventLoop):
        self._controller_api = controller_api
        # Setup using self.param1 directly
```
