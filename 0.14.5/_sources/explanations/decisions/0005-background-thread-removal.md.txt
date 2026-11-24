# 5. Remove Background Thread from Backend

Date: 2025-01-24

**Related:** [PR #98](https://github.com/DiamondLightSource/FastCS/pull/98)

## Status

Accepted

## Context

The FastCS Backend implementation uses `asyncio.run_coroutine_threadsafe()` to execute controller operations on a background event loop thread managed by `AsyncioDispatcher`. The system needs a simpler concurrency model that uses native async/await patterns and allows transports to manage their own threading if needed.

## Decision

Remove the background thread from Backend, making it fully async, while allowing specific transports to use a background thread if required. Backend should accept an event loop from the caller and use native async/await throughout. Transports that need threading (like Tango) manage their own threading explicitly.

Key architectural changes:
- Backend receives event loop from caller (no background dispatcher)
- Initialization uses `loop.run_until_complete()` instead of cross-thread scheduling
- Backend exposes `async serve()` method using native async/await patterns
- Scan tasks use `Task` objects from `loop.create_task()` instead of `Future` objects
- Transports that need threading create their own `AsyncioDispatcher` when needed

## Consequences

### Benefits

- **Simpler Concurrency Model:** Single event loop for most operations, no cross-thread coordination needed
- **Task-Based API:** Consistent use of `Task` objects with standard cancellation support
- **Composability:** `async serve()` can be composed with other async operations
