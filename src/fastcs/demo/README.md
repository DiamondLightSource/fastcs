# `fastcs.demo`

The demo package ships FastCS's **living example controllers** for the
ophyd-async / FastCS API-convergence refactor
([issue #388](https://github.com/DiamondLightSource/fastcs/issues/388), ADRs
0013–0019). Consolidated here (rather than a top-level `examples/` package) to
mirror ophyd-async, so the examples install with `fastcs[demo]` and can be run,
imported, and — crucially — used as the **single source of the tutorial code**.

These modules are the canonical source the tutorials `literalinclude` from
(one tutorial per example, see the docs `tutorials/`). They are kept green
under `uv run --locked tox`, so the tutorials cannot drift from the framework:
every framework PR that changes an API updates the example(s) it affects in the
*same* PR. This replaces the old "hand-authored `docs/snippets/` that drift"
approach — the examples are the docs.

## The five examples — a hello-world → complicated-device ladder

Two hardware backends: a temperature-controller sim (steps 1–4) and a
cut-down Eiger REST sim (step 5). Step 1 is pure-soft (no backend). Each rung
introduces exactly one new concept.

| # | Module | Concept | Backend | Issue |
|---|--------|---------|---------|-------|
| 1 | `hello_world.py` | pure-soft `@attr`/`@attr_rw` decorator over in-memory values | none (soft) | [#398](https://github.com/DiamondLightSource/fastcs/issues/398) |
| 2 | `temperature_attr.py` | callback getter/setter via the `attr` factory in `__init__` | temperature sim | [#404](https://github.com/DiamondLightSource/fastcs/issues/404) |
| 3 | `controllers.py` | reusable per-attribute `io=` `ReadWriteIO` objects, sub-controllers/vectors, `@scan`/`@command` | temperature sim | [#390](https://github.com/DiamondLightSource/fastcs/issues/390) |
| 4 | `temperature_scpi.py` (+ `scpi.py`) | declarative annotated attributes; `ControllerFiller` builds each `io` from **static** `SCPIParam` extras metadata | temperature sim | [#405](https://github.com/DiamondLightSource/fastcs/issues/405) |
| 5 | `eiger.py` (+ `simulation/eiger.py`) | introspectable device: bare hints filled from a **runtime** REST parameter tree | Eiger REST sim | [#391](https://github.com/DiamondLightSource/fastcs/issues/391) |

Notes:

- **Steps 2 vs 3** are the same device wired two ways — inline per-attribute
  getter/setter, then the same IO factored into a reusable `io=` object — a
  natural refactoring story on one backend.
- **Step 4 is deliberately *not* introspectable.** A SCPI device does not
  describe itself, which is exactly why you hand-annotate: the metadata lives
  in your Python (`SCPIParam("P", precision=3, …)`), not on the wire. Do **not**
  invent SCPI introspection — that would erase the contrast with step 5. The
  example `SCPIController`/`SCPIParam` vocabulary lives *here in the demo*, not
  in core FastCS (decision 3: core ships no extras vocabulary for 1.0); it
  demonstrates how a protocol layer builds on the filler's `(child, extras)`
  mechanism.
- **Step 5 uses a separate Eiger REST backend on purpose.** Introspection earns
  its complexity only when a device's parameters aren't knowable at author time
  (a detector, not a fixed-command temp controller). The backend switch *is*
  the lesson — "small & known → declare; large & self-describing → introspect"
  — and the REST sim also exercises an HTTP client backend the temp examples
  never touch, matching real downstream drivers (`fastcs-eiger`, `fastcs-secop`,
  PandABlocks).

## Baselines vs framework PRs

Steps 2, 3, 5 have current-API baselines that can be written **now**
(deliberately messy against the pre-refactor API) and are cleaned up as each
framework PR lands. Steps 1 and 4 need framework work first (`attr` factory
#397; `ControllerFiller` #394). See each issue's `Blocked by:` line.

`literalinclude` region markers are added to each module as part of writing its
tutorial (the umbrella docs pass,
[#408](https://github.com/DiamondLightSource/fastcs/issues/408)), not up front.
