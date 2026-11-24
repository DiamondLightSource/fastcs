# Datatypes

FastCS uses a datatype system to map Python types to attributes with additional
metadata for validation, serialization, and transport handling.

## Supported Types

FastCS defines `DType` as the union of supported Python types:

:::{literalinclude} ../../src/fastcs/datatypes/datatype.py
:start-at: "DType = ("
:end-at: ")"
:::

Each has a corresponding `DataType` class.

## Scalar Datatypes

### Int and Float

Both inherit from `_Numeric`, which adds support for bounds and alarm limits:

:::{literalinclude} ../../src/fastcs/datatypes/_numeric.py
:start-at: "@dataclass(frozen=True)"
:end-at: "max_alarm:"
:::

### Bool

Maps to Python `bool`. Initial value is `False`.

:::{literalinclude} ../../src/fastcs/datatypes/bool.py
:pyobject: Bool
:::

### String

Maps to Python `str`. Has an optional `length` field that truncates values during validation. It is also used as a hint by some transports to configure the size of string records (e.g. EPICS CA string waveform records).

:::{literalinclude} ../../src/fastcs/datatypes/string.py
:pyobject: String
:::

## Enum Datatype

Wraps a Python `enum.Enum` class:

:::{literalinclude} ../../src/fastcs/datatypes/enum.py
:pyobject: Enum
:::

The `Enum` datatype provides helper properties:

- `members`: List of enum values
- `names`: List of enum member names
- `index_of(value)`: Get the index of a value in the members list

:::{note}
FastCS uses enum **member names** (not values) when exposing choices to transports and
PVI. This means member names are the user-friendly UI strings while values are the
strings sent to the device:

```python
class DetectorStatus(StrEnum):
    Idle = "IDLE_STATE"
    Running = "RUNNING_STATE"
    Error = "ERROR_STATE"
```

Clients will see the choices as `["Idle", "Running", "Error"]`.

For UI strings with spaces, use the functional `enum.Enum` API with a dict:

```python
import enum
from fastcs.datatypes import Enum

DetectorStatus = Enum(enum.Enum("DetectorStatus", {"Run Finished": "RUN_FINISHED", "In Progress": "IN_PROGRESS"}))
```

Clients will see the choices as `["Run Finished", "In Progress"]`.
:::

## Array Datatypes

### Waveform

For homogeneous numpy arrays (spectra, images):

:::{literalinclude} ../../src/fastcs/datatypes/waveform.py
:pyobject: Waveform
:::

Validation ensures the array fits within the declared shape and has the correct dtype.

### Table

For structured numpy arrays with named columns:

:::{literalinclude} ../../src/fastcs/datatypes/table.py
:pyobject: Table
:::

The `structured_dtype` field is a list of `(name, dtype)` tuples following
numpy's structured array conventions.

## Validation

### Built-in Numeric Validation

`Int` and `Float` datatypes support min/max limits and alarm thresholds:

```python
from fastcs.attributes import AttrRW
from fastcs.datatypes import Int, Float

# Integer with bounds
count = AttrRW(Int(min=0, max=100))

# Float with units and alarm limits
temperature = AttrRW(Float(
    units="degC",
    min=-273.15,           # Absolute minimum
    max=1000.0,            # Absolute maximum
    min_alarm=-50.0,       # Warning below this
    max_alarm=200.0,       # Warning above this
))
```

#### Validation Behavior

```python
temp = Float(min=0.0, max=100.0)

temp.validate(50.0)   # Returns 50.0
temp.validate(-10.0)  # Raises ValueError: "Value -10.0 is less than minimum 0.0"
temp.validate(150.0)  # Raises ValueError: "Value 150.0 is greater than maximum 100.0"
```

### String Length

Limit the display length of strings:

```python
from fastcs.datatypes import String

# Limit display to 40 characters
status = AttrR(String(length=40))
```

:::{note}
The `length` parameter truncates values during validation and is also used by some
transports to configure their records, for example the EPICS CA transport uses it to
set the length of string waveform records.
:::

### Type Coercion

All datatypes automatically coerce compatible types:

```python
from fastcs.datatypes import Int, Float

int_type = Int()
int_type.validate("42")     # Returns 42 (str -> int)
int_type.validate(3.7)      # Returns 3 (float -> int, truncated)

float_type = Float()
float_type.validate("3.14") # Returns 3.14 (str -> float)
float_type.validate(42)     # Returns 42.0 (int -> float)
```

### When Validation Runs

Validation runs automatically when:

1. **Attribute update**: `await attr.update(value)` validates before storing
2. **Put request**: `await attr.put(value)` validates before sending to device
3. **Initial value**: Values passed to `initial_value` are validated on creation

```python
from fastcs.attributes import AttrRW
from fastcs.datatypes import Int

attr = AttrRW(Int(min=0, max=10), initial_value=5)

# Updates are validated
await attr.update(7)    # OK
await attr.update(15)   # Raises ValueError

# Puts are validated
await attr.put(3)       # OK
await attr.put(-1)      # Raises ValueError
```

## Transport Handling

Transports are responsible for serializing datatypes appropriately for their protocol.
Each transport must handle all supported datatypes. The datatype's `dtype` property
and class type are used to determine serialization:

- Scalars (`Int`, `Float`, `Bool`, `String`) serialize directly
- `Enum` values are typically serialized as integers (index) or strings (name)
- `Waveform` and `Table` arrays are serialized as lists or protocol-specific array types

## Creating Custom Datatypes

All datatypes inherit from `DataType[DType_T]`, a generic frozen dataclass that defines
the interface for type handling:

:::{literalinclude} ../../src/fastcs/datatypes/datatype.py
:start-at: "@dataclass(frozen=True)"
:end-at: "raise NotImplementedError()"
:::

### Required Properties

To create a custom datatype, subclass `DataType` or one of the existing datatypes and
implement the required properties:

**`dtype`**: Returns the underlying Python type. This is used for type coercion in
`validate()` and for transport serialization.

**`initial_value`**: Returns the default value used when an attribute is created
without an explicit initial value.

### Overriding `validate()`

The base `validate()` implementation attempts to cast incoming values to the target type:

:::{literalinclude} ../../src/fastcs/datatypes/datatype.py
:pyobject: DataType.validate
:::

Subclasses can override this to add validation logic. The pattern is

1. Coerce input to help type casting succeed - e.g. `Waveform` calls `numpy.asarray(...)`
2. Call `super().validate(value)` to call parent implementation and perform the type cast
3. Perform any additional validation such as checking limits - e.g. `_Numeric` adds min/max validation:

:::{literalinclude} ../../src/fastcs/datatypes/_numeric.py
:pyobject: _Numeric.validate
:::

### Overriding `equal()`

The `equal()` method is used by the `always` flag in attribute callbacks to determine
if a value has changed. The default uses Python's `==` operator, but array types
override this to use `numpy.array_equal()`:

:::{literalinclude} ../../src/fastcs/datatypes/waveform.py
:pyobject: Waveform.equal
:::

### Transport Compatibility

When creating a new datatype, existing transports will need to be updated to handle it,
unless the datatype inherits from a supported type. In the latter case, the transport
will use the parent class handling, while the custom datatype can add validation or
other behaviour on top.
