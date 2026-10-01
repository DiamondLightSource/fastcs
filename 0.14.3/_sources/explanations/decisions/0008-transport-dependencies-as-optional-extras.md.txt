# 8. Split Transport Dependencies into Optional Extras

Date: 2025-09-30

**Related:** [PR #221](https://github.com/DiamondLightSource/FastCS/pull/221)

## Status

Accepted

## Context

Currently all transport dependencies are installed regardless of which transports users actually needed.

Problems with required dependencies:
- Minimal installations bloated by unused transport dependencies
- Unclear dependency relationships for each transport
- No way to install just the core FastCS functionality

## Decision

Split transport dependencies into optional extras in `pyproject.toml`, allowing users to install only what they need.

The core FastCS package now requires only essential dependencies (pydantic, numpy, ruamel.yaml, IPython). Each transport is available as an optional extra, with convenience groups like `[all]`, `[epics]`, and `[dev]` for common installation patterns.

Key architectural changes:
- Core dependencies: pydantic, numpy, ruamel.yaml, IPython
- Individual transport extras: `[epicsca]`, `[epicspva]`, `[tango]`, `[graphql]`, `[rest]`
- Convenience groups: `[epics]`, `[all]`, `[dev]`, `[demo]`
- Each transport declares its own dependencies explicitly

## Consequences

### Benefits

- **Minimal Core Installation:** Users can install FastCS core without transport dependencies
- **Explicit Dependency Relationships:** Each transport declares what it needs
- **Flexible Installation:** Users choose exactly what they need: `pip install fastcs[epicspva,rest]`
- **Development Convenience:** `pip install fastcs[dev]` includes everything for development
- **Clear Documentation:** Installation commands are self-documenting

### Installation Patterns

**Minimal (core only):**
```bash
pip install fastcs
```

**Single transport:**
```bash
pip install fastcs[epicspva]  # EPICS PVA
pip install fastcs[rest]      # REST API
```

**Multiple transports:**
```bash
pip install fastcs[epics,rest]  # EPICS CA + PVA + REST
```

**All transports:**
```bash
pip install fastcs[all]
```

**Development:**
```bash
pip install fastcs[dev]
```
