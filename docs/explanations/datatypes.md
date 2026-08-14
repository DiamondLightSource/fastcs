# Datatypes

An attribute's datatype is a **python type**. Everything else that describes the
attribute - precision, units, limits, array shape - is **metadata**, passed as
keyword arguments and held on the attribute as `attr.meta`.

```python
from fastcs.attributes import AttrRW

temperature = AttrRW(float, precision=3, units="degC")
```

There is no `DataType` object to construct and no wrapper to unwrap: `attr.dtype`
is `float`, and `attr.meta` is a plain typed dict.

## Supported Types

FastCS defines `DType` as the union of supported python types:

:::{literalinclude} ../../src/fastcs/datatypes/types.py
:start-at: "DType = ("
:end-at: ")"
:::

## Scalar Datatypes

`int`, `float`, `bool` and `str` are used directly. Which metadata each accepts is
given by its `*Meta` typed dict:

| Datatype | Metadata                                              |
| -------- | ----------------------------------------------------- |
| `bool`   | `description`, `group`                                |
| `int`    | `description`, `group`, `units`, `limits`             |
| `float`  | `description`, `group`, `units`, `limits`, `precision` |
| `str`    | `description`, `group`, `length`                      |

`precision` is the number of decimal places a float is rounded to and displayed
with; it defaults to 2. `length` truncates a string during validation, and is
also a hint to transports sizing their records - the EPICS CA transport uses it
for string waveform records.

The constructors are overloaded per datatype, so metadata a datatype has no use
for is a type error rather than a field silently ignored:

```python
AttrRW(float, precision=3)  # fine
AttrRW(str, precision=3)    # type error, and raises at construction
```

## Enum Datatype

An `enum.Enum` subclass is used directly as the datatype; the choices come from
the class, so there is no metadata to give:

```python
import enum
from fastcs.attributes import AttrR

class DetectorStatus(enum.StrEnum):
    Idle = "IDLE_STATE"
    Running = "RUNNING_STATE"
    Error = "ERROR_STATE"

status = AttrR(DetectorStatus)
```

:::{note}
FastCS uses enum **member names** (not values) when exposing choices to transports and
PVI. This means member names are the user-friendly UI strings while values are the
strings sent to the device. For the enum above, clients see the choices as
`["Idle", "Running", "Error"]`.

For UI strings with spaces, use the functional `enum.Enum` API with a dict:

```python
import enum

DetectorStatus = enum.Enum(
    "DetectorStatus", {"Run Finished": "RUN_FINISHED", "In Progress": "IN_PROGRESS"}
)
```

Clients will see the choices as `["Run Finished", "In Progress"]`.
:::

## Array Datatypes

### Array1D

For homogeneous numpy arrays. The element type rides on the datatype itself, and
the maximum shape is metadata:

:::{literalinclude} ../../src/fastcs/datatypes/types.py
:start-at: "Array1D: TypeAlias"
:end-before: "class Table"
:::

```python
import numpy as np
from fastcs.attributes import AttrR
from fastcs.datatypes import Array1D

spectrum = AttrR(Array1D[np.float64], shape=(1000,))
image = AttrR(np.ndarray, array_dtype=np.uint16, shape=(1024, 1024))
```

Validation ensures the array fits within the declared shape and has the correct
element type. `shape` defaults to `(2000,)`.

### Table

For structured numpy arrays with named columns:

:::{literalinclude} ../../src/fastcs/datatypes/types.py
:pyobject: Table
:::

The `structured_dtype` metadata is a list of `(name, dtype)` tuples following
numpy's structured array conventions.

## Limits

Numeric limits are nested rather than flat, in four categories aligned with the
bluesky event-model:

:::{literalinclude} ../../src/fastcs/datatypes/limits.py
:pyobject: NumericLimits
:::

```python
from fastcs.attributes import AttrRW
from fastcs.datatypes import Limits, NumericLimits

temperature = AttrRW(
    float,
    units="degC",
    limits=NumericLimits(
        control=Limits(-273.15, 1000.0),  # what it may be driven to
        display=Limits(0.0, 500.0),       # what it is shown as spanning
        alarm=Limits(-50.0, 200.0),       # outside this it is in alarm
    ),
)
```

Only the **control** range rejects values. Display, alarm and warning are served
to clients - EPICS `LOPR`/`HOPR` and `DRVL`/`DRVH`, Tango's attribute
properties, the PVA display and alarm structures - but do not constrain a write.

## Validation

### Numeric limits

```python
from fastcs.datatypes import Limits, Meta, NumericLimits, validate_value

meta = Meta(limits=NumericLimits(control=Limits(0.0, 100.0)))

validate_value(float, meta, 50.0)   # Returns 50.0
validate_value(float, meta, -10.0)  # Raises ValueError: "Value -10.0 is less than minimum 0.0"
validate_value(float, meta, 150.0)  # Raises ValueError: "Value 150.0 is greater than maximum 100.0"
```

### Type Coercion

Values are coerced to the datatype:

```python
from fastcs.datatypes import Meta, validate_value

validate_value(int, Meta(), "42")     # Returns 42 (str -> int)
validate_value(int, Meta(), 3.7)      # Returns 3 (float -> int, truncated)
validate_value(float, Meta(), "3.14") # Returns 3.14 (str -> float)
validate_value(float, Meta(), 42)     # Returns 42.0 (int -> float)
```

### When Validation Runs

Validation runs automatically when:

1. **Attribute update**: `await attr.update(value)` validates before storing
2. **Set request**: `await attr.set(value)` validates before sending to device
3. **Initial value**: Values passed to `initial_value` are validated on creation

```python
from fastcs.attributes import AttrRW
from fastcs.datatypes import Limits, NumericLimits

attr = AttrRW(int, limits=NumericLimits(control=Limits(0, 10)), initial_value=5)

# Updates are validated
await attr.update(7)    # OK
await attr.update(15)   # Raises ValueError

# Sets are validated
await attr.set(3)       # OK
await attr.set(-1)      # Raises ValueError
```

Metadata itself is validated when the attribute is built, so a field that the
datatype has no use for fails fast even when it arrived without a static check -
from a declarative extras object, say:

```python
AttrR(str, precision=3)
# TypeError: 'precision' is not valid metadata for str attribute - valid fields
# are description, group, length
```

## Transport Handling

Transports are responsible for serializing values appropriately for their
protocol, and each must handle every supported datatype. They dispatch on
`attr.dtype` and read what they serve from `attr.meta`:

- Scalars (`int`, `float`, `bool`, `str`) serialize directly
- Enum values are typically serialized as integers (index) or strings (name)
- Arrays and tables are serialized as lists or protocol-specific array types

An array and a table are both held as `np.ndarray`; what separates them is that a
table's metadata names its columns, so a transport that needs to tell them apart
checks for `structured_dtype` in `attr.meta`.

## Adding a Datatype

A datatype is a python type in `DType`, so adding one means widening that union
and teaching the pieces that dispatch on it:

1. Add the type to `DType` in `fastcs.datatypes.types`, and to `resolve_datatype`
2. Add a `*Meta` typed dict for the metadata it accepts, and map the datatype to
   it in `meta_class_for`
3. Handle it in `validate_value`, `default_value` and `values_equal`
4. Add an overload to each of `AttrR`, `AttrW` and `AttrRW` so its metadata is
   statically checked
5. Handle it in each transport

Metadata alone needs much less: a new field on an existing `*Meta` is picked up
by `validate_meta` automatically, and only the transports that serve it change.
