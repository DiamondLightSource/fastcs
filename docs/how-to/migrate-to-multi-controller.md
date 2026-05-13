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

## 2. `controller:` → `controllers:` list of entries

The top-level singular `controller:` block is gone. Replace it with a
list of entries, each carrying an `id:`:

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
  - id: DEVICE                          # used as the addressing prefix
    type: my_driver.DeviceController    # required discriminator
    ip_address: "192.168.1.100"
    port: 25565

transport:
  - epicsca: {}
```

The entry's `id:` (here `DEVICE`) is used verbatim as the EPICS PV
prefix, the REST route prefix, the GraphQL top-level Query field, and
the Tango device-name segment. See
[Run Multiple Transports Simultaneously](multiple-transports.md) for
the per-transport id charset rules — GraphQL's `[A-Za-z_][A-Za-z0-9_]*`
is the lowest common denominator.

To host more than one controller, add more list entries. Duplicate ids
across entries are rejected at run time.

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
`controller.set_path(["DEVICE"])` (or set the id from the YAML entry's
`id:` field when using `launch()`).

## 4. `type:` discriminator is required on every entry

Each entry under `controllers:` carries a required `type:` discriminator
that names the Controller class to instantiate. The discriminator value
defaults to `<top-level-package>.<ClassName>` so that classes from
independently-distributed packages (e.g. `fastcs_eiger.Detector` and
`fastcs_pmac.Detector`) cannot collide on a short name. A class may
override this verbatim (no prefix added) by setting
`type_name: ClassVar[str]`. The same rule applies whether `launch()` is
called with a single class or with several — `type:` is never optional.

```yaml
# Two-class app: launch([Lakeshore, Eurotherm])
controllers:
  - id: CRYO
    type: fastcs_lakeshore.Lakeshore
    ip_address: "192.168.1.100"
  - id: OVEN
    type: fastcs_eurotherm.Eurotherm
    ip_address: "192.168.1.101"

transport:
  - epicsca: {}
```

## 5. Direct `FastCS(...)` usage is unchanged for the single-controller case

If you instantiate `FastCS` directly rather than via `launch()`, the
single-controller form `FastCS(controller, transports)` still works.
For multi-controller, pass a sequence:
`FastCS([controller_a, controller_b], transports)`. Each Controller
must have had `set_path([...])` called before being handed to `FastCS`.

## 6. GUI/docs emission output is now a directory

`EpicsGUIOptions.output_path` (single file) was renamed to
`output_dir` (directory). `EpicsDocsOptions.path` likewise renamed to
`output_dir`. Per-controller files (`<id>.bob`, `<id>.md`) plus an
`index.<ext>` are written into the directory — even when only one
controller is configured. Update any YAML or Python that set the old
field names.
