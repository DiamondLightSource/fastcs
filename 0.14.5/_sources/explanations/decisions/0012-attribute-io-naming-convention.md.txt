# 12. AttributeIO and AttributeIORef Naming Convention

Date: 2026-03-12

## Status

Accepted

## Context

The `AttributeIO` and `AttributeIORef` classes introduced in [ADR9](0009-handler-to-attribute-io-pattern.md)
have descriptive, unambiguous names that clearly communicate their role within fastcs.
However, their length leads to verbose class names when drivers subclass them:

```python
class TempControllerAttributeIO(AttributeIO[float, TempControllerIORef]): ...
class TempControllerIORef(AttributeIORef): ...
```

Alternative names were considered but none offered a meaningful improvement:

- Shorter names (e.g. `IO`, `Ref`) are too terse in isolation and lose the context that
  these are attribute-scoped constructs within fastcs.
- Alternative descriptive names introduce new terminology without reducing confusion.

The verbosity is felt most in driver code, where developers repeatedly type the full
parent class name. In the fastcs internals and documentation, the full names remain
appropriate.

## Decision

The `AttributeIO` and `AttributeIORef` class names are retained unchanged. Driver
authors are encouraged to introduce shorter driver-specific aliases by subclassing:

```python
class MyIO(AttributeIO[float, MyRef]): ...
class MyRef(AttributeIORef): ...
```

The subclass name is left to the driver author's discretion. The inheritance
relationship ensures the full fastcs name (`AttributeIO`, `AttributeIORef`) remains
visible in the class hierarchy and in documentation, preserving discoverability for
anyone unfamiliar with the driver.

## Consequences

- Driver code is less verbose without sacrificing clarity at the fastcs API level.
- The parent class is always visible via IDE tooling, so developers encountering a short
  name like `MyRef` can immediately understand its role by inspecting the inheritance.
- Over time, as developers become more familiar with the pattern, informal shorthand
  ("the IO", "the Ref") is expected to emerge in discussions to make this less of an issue.
