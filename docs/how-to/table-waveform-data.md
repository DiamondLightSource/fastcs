# Work with Table and Array Data

This guide shows how to use the `Array1D` and `Table` datatypes for array-based data.

## Array1D - Homogeneous Arrays

Use `Array1D` for numpy arrays of a single element type (spectra, time series, images).

```python
import numpy as np

from fastcs.attributes import AttrR, AttrRW
from fastcs.controllers import Controller
from fastcs.datatypes import Array1D

class SpectrumController(Controller):
    # 1D array of 1000 float64 values
    spectrum = AttrR(Array1D[np.float64], shape=(1000,))

    # Writable array
    setpoints = AttrRW(Array1D[np.float64], shape=(100,))
```

### 2D Arrays (Images)

`Array1D` is, as the name says, one dimensional. An array of higher rank has no
ophyd-async-compatible spelling, so write it as `np.ndarray` with an explicit
`array_dtype`:

```python
class CameraController(Controller):
    # 2D array for images (max 1024x1024 uint16)
    image = AttrR(np.ndarray, array_dtype=np.uint16, shape=(1024, 1024))

    # Smaller region of interest
    roi = AttrRW(np.ndarray, array_dtype=np.uint16, shape=(256, 256))
```

### Array Metadata

| Field | Type | Default | Description |
|-----------|------|---------|-------------|
| `array_dtype` | `DTypeLike` | from the datatype subscript | Numpy element type (`np.float64`, `np.int32`, etc.) |
| `shape` | `tuple[int, ...]` | `(2000,)` | Maximum array dimensions |

### Updating Arrays

```python
from fastcs.methods import scan

class SpectrumController(Controller):
    spectrum = AttrR(Array1D[np.float64], shape=(1000,))

    @scan(period=0.1)
    async def read_spectrum(self):
        # Get data from device (e.g., numpy array)
        data = await self.device.get_spectrum()

        # Update the attribute
        await self.spectrum.update(data)
```

### Shape Validation

Arrays validate that data fits within the declared shape:

```python
spectrum = AttrR(Array1D[np.float64], shape=(100,))

# OK - fits within shape
spectrum.validate(np.array([1.0, 2.0, 3.0]))

# Error - exceeds maximum shape
spectrum.validate(np.arange(200))  # ValueError: shape (200,) exceeds maximum (100,)
```

## Table - Structured Arrays

Use `Table` for tabular data with named columns of different types.

### Basic Table

```python
import numpy as np

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Table

class MeasurementController(Controller):
    # Table with columns: name (string), value (float), valid (bool)
    results = AttrR(
        Table,
        structured_dtype=[
            ("name", "S32"),       # 32-character string
            ("value", np.float64),
            ("valid", np.bool_),
        ],
    )
```

### Table Metadata

| Field | Type | Description |
|-----------|------|-------------|
| `structured_dtype` | `list[tuple[str, DTypeLike]]` | List of (name, dtype) tuples |

### Creating Table Data

```python
from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Table

class ChannelController(Controller):
    channel_data = AttrR(
        Table,
        structured_dtype=[
            ("channel", np.int32),
            ("temperature", np.float64),
            ("status", "S10"),
        ],
    )

# Create data using numpy structured array
data = np.array([
    (0, 25.5, "OK"),
    (1, 26.2, "OK"),
    (2, 30.1, "WARN"),
], dtype=[("channel", np.int32), ("temperature", np.float64), ("status", "S10")])

# Update the attribute
await controller.channel_data.update(data)
```

### Accessing Table Data

```python
# Get the table
table = controller.results.readback

# Access by column name
names = table["name"]
values = table["value"]

# Access by row index
first_row = table[0]

# Access specific cell
first_name = table[0]["name"]
```

### Common String Types in Tables

```python
# Fixed-length byte strings (ASCII)
("name", "S32")      # 32-byte string
("status", "S10")    # 10-byte string

# Unicode strings
("label", "U32")     # 32-character unicode string
```
