# 6. Create ControllerAPI Abstraction Layer

Date: 2025-03-10

**Related:** [PR #87](https://github.com/DiamondLightSource/FastCS/pull/87)

## Status

Accepted

## Context

Transports currently access `Controller` instances directly to extract attributes, methods, and metadata for serving over their protocols. This creates a few problems:

- **Tight Coupling:** Transports are coupled to internal Controller structure, making evolution difficult
- **Code Duplication:** Every transport re-implemented similar traversal logic for discovering attributes and methods
- **No Encapsulation:** Transports have direct access to mutable controller state
- **No Static View:** No complete, immutable snapshot of controller API after initialization

## Decision

Introduce `ControllerAPI` as an abstraction layer that provides transports with a complete, static, read-only representation of a controller's capabilities after initialization.

All transports now work with `ControllerAPI` instead of direct `Controller` access. A single `create_controller_api()` function handles all API extraction, replaces custom traversal logic in each transport.

Key architectural changes:
- `ControllerAPI` dataclass represents the complete, hierarchical structure of what a controller exposes
- Separate dictionaries for attributes, command_methods, put_methods, and scan_methods
- `walk_api()` method provides depth-first traversal of the API tree
- Backend creates ControllerAPI during initialization and passes to transports

## Consequences

### Benefits

- **Encapsulation:** Transports work with read-only API, cannot modify controller internals
- **Single Source of Truth:** One canonical representation of controller capabilities
- **Reduced Code Duplication:** Traversal and extraction logic written once, used by all transports
- **Separation of Concerns:** Controllers focus on device logic, ControllerAPI handles representation, transports focus on protocol
- **Testability:** Transports can be tested with synthetic ControllerAPIs; controllers tested independently
- **Evolution Independence:** Controller internals can change without affecting transports

### Migration Pattern

**Before (Direct Controller access):**
```python
class EpicsCAIOC:
    def __init__(self, pv_prefix: str, controller: Controller):
        # Each transport traverses controller itself
        for attr_name in dir(controller):
            attr = getattr(controller, attr_name)
            if isinstance(attr, Attribute):
                self._create_pv(f"{pv_prefix}{attr_name}", attr)
```

**After (ControllerAPI abstraction):**
```python
class EpicsCAIOC:
    def __init__(self, pv_prefix: str, controller_api: ControllerAPI):
        # Transport receives ready-to-use API structure
        for attr_name, attr in controller_api.attributes.items():
            self._create_pv(f"{pv_prefix}{attr_name}", attr)

        # Walk sub-controllers using standard method
        for sub_api in controller_api.walk_api():
            # Process sub-controllers with consistent structure
```
