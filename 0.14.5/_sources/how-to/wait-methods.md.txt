# Synchronize Operations with Wait Methods

This guide shows how to use `wait_for_value()` and `wait_for_predicate()` to synchronize
operations in your FastCS driver.

## Wait for a Specific Value

Use `wait_for_value()` to pause execution until an attribute reaches an exact value:

```python
from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Int
from fastcs.methods import command

class MotorController(Controller):
    position: AttrR[int] = AttrR(Int())
    target: AttrR[int] = AttrR(Int())

    @command()
    async def move_and_wait(self):
        """Move to target and wait until we arrive."""
        target = self.target.get()

        # Start the move (implementation depends on your device)
        await self._start_move(target)

        # Wait until position equals target (timeout after 30 seconds)
        await self.position.wait_for_value(target, timeout=30.0)
```

## Wait for a Condition with Predicates

Use `wait_for_predicate()` for more complex conditions. The predicate is a callable that
takes the attribute value and returns `True` when the condition is satisfied:

```python
from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Float
from fastcs.methods import command

class TemperatureController(Controller):
    temperature: AttrR[float] = AttrR(Float())

    @command()
    async def wait_for_stable(self):
        """Wait until temperature is within operating range."""

        def in_range(temp: float) -> bool:
            return 20.0 <= temp <= 25.0

        await self.temperature.wait_for_predicate(in_range, timeout=60.0)
```

## Handling Timeouts

Both methods raise `TimeoutError` if the condition isn't met within the timeout period.
The error message includes the current value for debugging:

```python
from fastcs.logging import logger

try:
    await self.position.wait_for_value(100, timeout=5.0)
except TimeoutError:
    logger.exception("Move timed out")
```

## Early Return for Already Satisfied Conditions

If the condition is already satisfied when called, both methods return immediately
without creating an internal event:

```python
# If position is already 100, this returns immediately
await self.position.wait_for_value(100, timeout=30.0)

# If temperature is already in range, this returns immediately
await self.temperature.wait_for_predicate(in_range, timeout=60.0)
```

## Concurrent Waits

Use `asyncio.gather()` to wait for multiple conditions simultaneously:

```python
import asyncio

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Float
from fastcs.methods import command

class MultiAxisController(Controller):
    x_position = AttrR(Float())
    y_position = AttrR(Float())
    z_position = AttrR(Float())

    @command()
    async def move_all_and_wait(self):
        """Wait for all axes to reach their targets."""
        await asyncio.gather(
            self.x_position.wait_for_value(10.0, timeout=30.0),
            self.y_position.wait_for_value(20.0, timeout=30.0),
            self.z_position.wait_for_value(5.0, timeout=30.0),
        )
```
