# 4. Rename Backend to Transport

Date: 2024-11-29

**Related:** [PR #67](https://github.com/DiamondLightSource/FastCS/pull/67)

## Status

Accepted

## Context

The original FastCS architecture used the term "backend" ambiguously to describe both the overall framework that managed controllers and the specific communication protocol implementations (EPICS CA, PVA, REST, Tango). This dual usage created confusion:

- It was unclear whether "backend" referred to the framework itself or the protocol layer
- The terminology didn't clearly differentiate between the abstract framework and the underlying communication mechanisms
- The inheritance pattern made it difficult to compose multiple transports or swap them dynamically

## Decision

Rename "backend" to "transport" for all protocol/communication implementations to clearly differentiate them from the framework.

The term "backend" can now refer to the overall FastCS framework/system, while "transport" specifically refers to protocol implementations (EPICS CA, PVA, REST, GraphQL, Tango). FastCS accepts Transport implementations as plugins, enabling flexible composition and loose coupling.

Key architectural changes:
- Introduce `TransportAdapter` abstract base class with standardized interface
- Move to composition-based architecture where transports are passed to `FastCS` rather than being subclasses
- Introduce `FastCS` class as the programmatic interface for running controllers with transports
- Add `launch()` function as the primary entry point for initializing controllers

## Consequences

### Benefits

- **Clear Terminology:** The separation between framework (backend) and protocol layer (transport) is now explicit
- **Consistent Architecture:** All transports follow the adapter pattern with a standardized interface
- **Flexible Composition:** Transports can be added, removed, or swapped at runtime
- **Improved Extensibility:** Adding new transport protocols is straightforward with the adapter pattern

### Migration Pattern

**Before (Inheritance hierarchy):**
```python
class EpicsBackend(Backend):
    def run(self):
        # Protocol-specific implementation

fastcs = FastCS(controller)  # Tightly coupled to framework
```

**After (Composition with Transport plugins):**
```python
transport = EpicsTransport(controller_api)
fastcs = FastCS(controller, [transport])
```
