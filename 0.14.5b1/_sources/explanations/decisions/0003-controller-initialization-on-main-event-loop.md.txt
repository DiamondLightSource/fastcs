# 3. Controller Initialization on Main Event Loop

Date: 2024-08-01

**Related:** [PR #49](https://github.com/DiamondLightSource/FastCS/pull/49)

## Status

Accepted

## Context

The Backend accepts a pre-created Mapping with no async initialization hook for controllers. Each backend subclass manually orchestrates initialization steps, leading to inconsistent lifecycle patterns. Controllers need a way to perform async setup before their API is exposed to transports.

## Decision

Move initialisation logic into Backend so that it:
- Creates the event loop
- Runs `controller.initialise()` before creating the Mapping
- Creates the Mapping from the initialized controller
- Runs initial tasks including `controller.connect()`

Controller then has two hooks: `initialise()` for pre-API setup (hardware introspection, dynamic attribute creation) and `connect()` for post-API connection logic.

## Consequences

The new design the initialisation of the application is easier to understand and makes the API for writing controllers and transports simpler and more flexible.

### Migration Pattern

For controller developers:

```python
class MyController(Controller):
    async def initialise(self) -> None:
        # Async setup before API creation (introspect hardware, create attributes)
        await self._hardware.connect()

    async def connect(self) -> None:
        # Async setup after API creation (establish connections)
        await self._hardware.initialize()
```
