# `fastcs.demo`

The demo package ships FastCS's **living example controllers** for the
ophyd-async / FastCS API-convergence refactor
([issue #388](https://github.com/DiamondLightSource/fastcs/issues/388), ADRs
0013–0019). Consolidated here (rather than a top-level `examples/` package) to
mirror ophyd-async, so the examples install with `fastcs[demo]` and can be run,
imported, and — crucially — used as the **single source of the tutorial code**.

These modules are the canonical source the tutorials `literalinclude` from
(see the docs `tutorials/`). They are kept green under `uv run --locked tox`,
so the tutorials cannot drift from the framework: every framework PR that
changes an API updates the example(s) it affects in the *same* PR. This
replaces the old "hand-authored `docs/snippets/` that drift" approach — the
examples are the docs.

## The example modules — a hello-world → complicated-device ladder

Two hardware backends: a temperature-controller sim and a cut-down Eiger REST
sim. The hello-world is pure-soft (no backend). IO is supplied as plain
`getter`/`setter` callables on `AttrR`/`AttrW`/`AttrRW` (or the `@attr`
decorator) — there is no `io=` object and no `DataType`.

| Module | Concept | Backend | Issue |
|--------|---------|---------|-------|
| `hello_world.py` | pure-soft `@attr` decorator over in-memory values | none (soft) | [#398](https://github.com/DiamondLightSource/fastcs/issues/398) |
| `temperature_attr.py` | `getter`/`setter` callables in `__init__` (`AttrRW(getter=…, setter=…)`), then composition & methods: sub-controllers / `ControllerVector`, `@scan`, `@command` | temperature sim | [#404](https://github.com/DiamondLightSource/fastcs/issues/404), [#390](https://github.com/DiamondLightSource/fastcs/issues/390) |
| `temperature_scpi.py` (+ `scpi.py`) | declarative annotated attributes; `ControllerFiller` builds each getter/setter from **static** `SCPIParam` extras metadata | temperature sim | [#405](https://github.com/DiamondLightSource/fastcs/issues/405) |
| `eiger.py` (+ `simulation/eiger.py`) | introspectable device: bare hints filled from a **runtime** REST parameter tree | Eiger REST sim | [#391](https://github.com/DiamondLightSource/fastcs/issues/391) |

## The four tutorials

Four modules, **four** tutorials (the old "reusable `io=` object" rung is gone —
`io=` objects were replaced by getter/setter callables, so there is nothing to
factor into):

1. **hello world** — `hello_world.py` (soft `@attr`).
2. **getter/setter** — `temperature_attr.py`; the full multi-ramp temperature
   controller, so this is also where **composition + `@scan` + `@command`**
   are shown (#390). Closes with *"when the shared pattern is worth naming,
   reach for the declarative style →"*.
3. **declarative** — `temperature_scpi.py` (annotated `SCPIParam` + filler).
4. **introspectable** — `eiger.py`.

Notes:

- **The declarative style is the DRY answer for a real protocol family**, not
  a reusable IO object. Recommend it when the shared wire pattern is worth
  naming (a protocol you'll reuse); for a handful of bespoke attributes,
  getter/setter in `__init__` is lighter and fine.
- **`temperature_scpi.py` is deliberately *not* introspectable.** A SCPI device
  does not describe itself, which is exactly why you hand-annotate: the metadata
  lives in your Python (`SCPIParam("P", precision=3, …)`), not on the wire. Do
  **not** invent SCPI introspection — that would erase the contrast with the
  Eiger example. The `SCPIController`/`SCPIParam` vocabulary lives *here in the
  demo*, not in core FastCS (decision 3: core ships no extras vocabulary for
  1.0); it demonstrates how a protocol layer builds on the filler's
  `(child, extras)` mechanism.
- **`eiger.py` uses a separate REST backend on purpose.** Introspection earns
  its complexity only when a device's parameters aren't knowable at author time
  (a detector, not a fixed-command temp controller). The backend switch *is*
  the lesson — "small & known → declare; large & self-describing → introspect"
  — and the REST sim also exercises an HTTP client backend the temp examples
  never touch, matching real downstream drivers (`fastcs-eiger`, `fastcs-secop`,
  PandABlocks).

## Baselines vs framework PRs

`temperature_attr.py` and `eiger.py` have current-API baselines that can be written **now** (deliberately messy against the
pre-refactor API) and are cleaned up as each framework PR lands. `hello_world.py`
and `temperature_scpi.py` need framework work first (`@attr` #397;
`ControllerFiller` #394). See each issue's `Blocked by:` line.

`literalinclude` region markers are added to each module as part of writing its
tutorial (the umbrella docs pass,
[#408](https://github.com/DiamondLightSource/fastcs/issues/408)), not up front.
