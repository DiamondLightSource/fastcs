# examples/

This package is the **living review artifact** for the FastCS / ophyd-async
API-convergence refactor tracked in
[issue #388](https://github.com/DiamondLightSource/fastcs/issues/388) and its
[ADRs](../docs/explanations/decisions/) (0013-0019).

It exists so that every framework PR in the refactor has something concrete
to run against, alongside the unit test suite: three example controllers,
each written in one of the three styles the refactor is converging FastCS
towards. As each framework PR lands (`AttributeIO` rework, `ControllerFiller`,
typed commands, the setpoint cache/timestamps/`ControllerRunner`, the naming
pass, `@attr_rw` sugar), the example(s) it affects are updated in the *same*
PR, so `examples/` always reflects current `main` and stays green under
`uv run --locked tox`. It is not a tutorial or documentation snippet source —
those live under `docs/`.

## The three styles

1. **IORef temperature controller** (`examples/example_1_ioref/`, tracked by
   the `DRAFT: Example 1` sub-issue of #388). A deliberately-messy adaptation
   of `src/fastcs/demo/controllers.py`, written against **whatever the
   current API is** at the time it's updated. It starts on the *pre-refactor*
   `AttributeIORef`/class-scope-instance API and is gradually cleaned up as
   each framework PR lands — e.g. once
   [ADR 14](../docs/explanations/decisions/0014-attribute-io-rw-rework.md)'s
   `AttributeIO` rework merges, this example's `io_ref=`/`ios=[...]` wiring
   is updated to `io=` in that same PR. This is intentional: it is the
   baseline that proves each framework PR doesn't break a real (if simple)
   driver, and it visibly tracks the migration path a downstream repo like
   `fastcs-eiger` or `fastcs-catio` would need to follow.

2. **Introspectable Eiger-style controller** (`examples/example_2_introspectable/`,
   tracked by the `DRAFT: Example 2` sub-issue of #388). Mirrors a REST API
   the way `fastcs-eiger`, `fastcs-secop`, and `fastcs-PandABlocks` do today:
   attributes are built from data queried at `initialise()` time, not
   declared as class-scope instances. This is the example that exercises
   [ADR 13](../docs/explanations/decisions/0013-declarative-procedural-split-and-controller-filler.md)'s
   `ControllerFiller` and bare-hint declarations once that PR lands.

3. **PyTango-style `@attr_rw` device** (`examples/example_3_decorator/`,
   tracked by the `DRAFT: Example 3` sub-issue of #388, blocked on the
   `@attr_rw` decorator-sugar issue). A trivial getter/setter device in the
   style of
   [ADR 18](../docs/explanations/decisions/0018-attr-decorator-sugar.md),
   demonstrating the simple case FastCS needs to match PyTango on.

## Status

**Scaffold only.** This PR (the ADR seed) adds this package, its structure,
and this README — it does not implement the framework changes or the three
example controllers themselves. Each example is implemented by its own
tracked sub-issue of #388, against the framework API as it exists once that
issue's dependencies have landed. See the `Blocked by:` lines on each
`DRAFT:` sub-issue for the landing order.

## Keeping it green

Every framework PR that touches `src/fastcs/` and affects one of these three
styles must update the corresponding example(s) in the same PR, and
`uv run --locked tox` must pass. This is the acceptance criterion listed on
every sub-issue of #388 for exactly this reason: it keeps the examples
honest as a description of "how do I actually write a FastCS driver today,"
rather than letting them drift out of sync with the framework the way
standalone documentation snippets can.
