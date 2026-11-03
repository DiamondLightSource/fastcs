# 11. Introduce ControllerVector for Indexed Sub-Controllers

Date: 2025-11-10

**Related:** [PR #192](https://github.com/DiamondLightSource/FastCS/pull/192)

## Status

Accepted

## Context

Many devices have multiple identical components that need individual control: multi-axis motion stages, multi-channel power supplies, multiple file writers, etc. Developers must manually register each indexed controller with string-based names and check for this pattern throughout the code to perform different logic

```python
for i in range(num_axes):
    motor = MotorController()
    self.add_sub_controller(f"Axis{i}", motor)  # String-based naming
```

This approach lacks collection semantics. Accessing controllers requires string manipulation (`controller.sub_controllers["Axis0"]`) and heuristics to test if an attribute is numerical, rather than natural indexing (`controller.axes[0]`).

## Decision

Introduce `ControllerVector`, a specialized controller type for managing collections of indexed sub-controllers with dict-like semantics.

`ControllerVector` implements `MutableMapping` with integer-only keys, providing natural indexing, iteration, and length operations. It supports non-contiguous indices and can have shared attributes alongside the indexed sub-controllers.

Key architectural changes:
- `ControllerVector` implements `__getitem__`, `__setitem__`, `__iter__`, `__len__`
- Keys enforced to be integers only
- Supports sparse indexing: `{1: motor1, 5: motor5, 10: motor10}`
- Can be subclassed to add shared attributes
- Inherits from `BaseController` for full integration with controller hierarchy

## Consequences

### Benefits

- **Natural Collection Semantics:** Dict-like interface provides familiar indexing and iteration
- **Consistency:** Integer-only keys prevent naming inconsistencies
- **Clear Intent:** ControllerVector explicitly signals "collection of identical components"
- **Sparse Collections:** Non-contiguous indices support flexible numbering schemes
- **Type Safety:** Integer indexing enforced by type hints and runtime checks
- **Shared Attributes:** Can add attributes to the vector itself, separate from indexed components

### Migration Pattern

**Before (Manual registration):**
```python
class StageController(Controller):
    def __init__(self, num_axes: int):
        super().__init__()
        for i in range(num_axes):
            motor = MotorController()
            self.add_sub_controller(f"Axis{i}", motor)

        # Access via string keys
        first = self.sub_controllers["Axis0"]
```

**After (ControllerVector):**
```python
class StageController(Controller):
    def __init__(self, num_axes: int):
        super().__init__()
        self.axes = ControllerVector(
            {i: MotorController() for i in range(num_axes)},
            description="Motor axes"
        )

        # Natural dict-like access
        first = self.axes[0]
        for i, motor in self.axes.items():
            print(f"Motor {i}: {motor}")
```
