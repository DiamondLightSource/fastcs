# 10. Remove SubController Class

Date: 2025-10-01

**Related:** [PR #222](https://github.com/DiamondLightSource/FastCS/pull/222)

## Status

Accepted

## Context

FastCS provides two separate classes for building controller hierarchies: `Controller` for top-level controllers and `SubController` for nested components. This has become a purely philosophical distinction and now just adds limitations for no benefit:

- **Design-Time Commitment:** Developers have to choose class at definition time, before knowing all contexts where components might be used
- **Reduced Reusability:** A component designed as `SubController` can't be reused as a top-level controller without changing its base class

## Decision

Unify `Controller` and `SubController` into a single `Controller` class that can be used in both top-level and nested contexts. Whether a Controller is "top-level" or "nested" is now determined by how it is used, not by its class.

Key architectural changes:
- Remove `SubController` class entirely
- Move `root_attribute` property to `Controller`
- Any Controller instance can now be nested in any other Controller

## Consequences

### Benefits

- **Composition over Inheritance:** Hierarchy determined by usage, not class definition
- **Increased Reusability:** Controllers work in any context without refactoring
- **Simpler Mental Model:** One class for all controller use cases
- **Reduced Coupling:** No design-time commitment to hierarchy level
- **Easier Evolution:** Controllers can start standalone and be nested later

### Migration Pattern

**Before (Two classes):**
```python
from fastcs import Controller, SubController

class RampController(SubController):  # Forced to use SubController
    start = AttrRW(Int())
    end = AttrRW(Int())

class TempController(Controller):  # Forced to use Controller
    def __init__(self):
        super().__init__()
        self.register_sub_controller("Ramp1", RampController())
```

**After (One class):**
```python
from fastcs import Controller

class RampController(Controller):  # Just use Controller
    start = AttrRW(Int())
    end = AttrRW(Int())

class TempController(Controller):  # Just use Controller
    def __init__(self):
        super().__init__()
        self.add_sub_controller("Ramp1", RampController())

# RampController can now be used as a top-level controller or nested
```
