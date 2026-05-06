# Migrate to Multi-Controller FastCS

FastCS now supports more than one top-level Controller per application.
The launch-framework config schema, the EPICS option dataclasses, and
the bundled demo all changed shape to accommodate this. This guide
covers the manual migration steps for an existing FastCS app.

## 1. Rename `controller.yaml` → `fastcs.yaml`

The bundled demo's config file moved from
`src/fastcs/demo/controller.yaml` to `src/fastcs/demo/fastcs.yaml`. The
name `fastcs.yaml` is now the recommended convention for application
configs, but the launcher does not hard-code it — `python -m my_driver
run <path>` still accepts any path. If you rely on the demo path
explicitly (e.g. in a `launch.json` debug config), update it.

## 2. `controller:` → `controllers: { <id>: ... }`

The top-level singular `controller:` block is gone. Replace it with a
dict keyed by controller id:

```yaml
# Before
controller:
  ip_address: "192.168.1.100"
  port: 25565

transport:
  - epicsca: {}
```

```yaml
# After
controllers:
  DEVICE:                     # id — used as the addressing prefix
    ip_address: "192.168.1.100"
    port: 25565

transport:
  - epicsca: {}
```

The dict key (here `DEVICE`) is the controller id. It is used verbatim
as the EPICS PV prefix, the REST route prefix, the GraphQL top-level
Query field, and the Tango device-name segment. See
[Run Multiple Transports Simultaneously](multiple-transports.md) for
the per-transport id charset rules — GraphQL's `[A-Za-z_][A-Za-z0-9_]*`
is the lowest common denominator.

To host more than one controller, add more dict entries. Duplicate ids
are rejected at config-load time.

## 3. Drop `pv_prefix` from `EpicsIOCOptions`

`EpicsIOCOptions` and its `pv_prefix` field are removed. The PV prefix
is now derived from the controller id, so a transport block that used
to look like:

```yaml
# Before
transport:
  - epicsca:
      pv_prefix: DEVICE
```

becomes:

```yaml
# After
transport:
  - epicsca: {}
```

The same applies to PVA. If you construct transports in Python rather
than via YAML, replace `EpicsCATransport(epicsca=EpicsIOCOptions(
pv_prefix="DEVICE"))` with `EpicsCATransport()` plus
`controller.set_id("DEVICE")` (or set the id from the YAML key when
using `launch()`).

## 4. `type:` discriminator and single-class inference

Each entry under `controllers:` carries a `type:` discriminator that
names the Controller class to instantiate. When `launch()` is called
with a single class, `type:` may be omitted — it defaults to that
class's discriminator (the class `__name__`, or
`type_name: ClassVar[str]` on the class if set). When `launch()` is
called with more than one class, every entry must carry an explicit
`type:`.

```yaml
# Two-class app: launch([Lakeshore, Eurotherm])
controllers:
  CRYO:
    type: Lakeshore
    ip_address: "192.168.1.100"
  OVEN:
    type: Eurotherm
    ip_address: "192.168.1.101"

transport:
  - epicsca: {}
```

## 5. Direct `FastCS(...)` usage is unchanged for the single-controller case

If you instantiate `FastCS` directly rather than via `launch()`, the
single-controller form `FastCS(controller, transports)` still works.
For multi-controller, pass a sequence:
`FastCS([controller_a, controller_b], transports)`. Each Controller
must have had `set_id(...)` called before being handed to `FastCS`.

## 6. GUI/docs emission output is now a directory

`EpicsGUIOptions.output_path` (single file) was renamed to
`output_dir` (directory). `EpicsDocsOptions.path` likewise renamed to
`output_dir`. Per-controller files (`<id>.bob`, `<id>.md`) plus an
`index.<ext>` are written into the directory — even when only one
controller is configured. Update any YAML or Python that set the old
field names.
