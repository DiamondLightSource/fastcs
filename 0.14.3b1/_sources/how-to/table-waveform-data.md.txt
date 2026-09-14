# Work with Table and Waveform Data

This guide shows how to use `Waveform` and `Table` datatypes for array-based data.

## Waveform - Homogeneous Arrays

Use `Waveform` for numpy arrays of a single data type (spectra, time series, images).

### Basic 1D Waveform

```python
import numpy as np

from fastcs.attributes import AttrR, AttrRW
from fastcs.controllers import Controller
from fastcs.datatypes import Waveform

class SpectrumController(Controller):
    # 1D array of 1000 float64 values
    spectrum: AttrR[np.ndarray] = AttrR(Waveform(np.float64, shape=(1000,)))

    # Writable waveform
    setpoints: AttrRW[np.ndarray] = AttrRW(Waveform(np.float64, shape=(100,)))
```

### 2D Waveform (Images)

```python
class CameraController(Controller):
    # 2D array for images (max 1024x1024 uint16)
    image: AttrR[np.ndarray] = AttrR(Waveform(np.uint16, shape=(1024, 1024)))

    # Smaller region of interest
    roi: AttrRW[np.ndarray] = AttrRW(Waveform(np.uint16, shape=(256, 256)))
```

### Waveform Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `array_dtype` | `DTypeLike` | (required) | Numpy dtype (`np.float64`, `np.int32`, etc.) |
| `shape` | `tuple[int, ...]` | `(2000,)` | Maximum array dimensions |

### Updating Waveforms

```python
from fastcs.methods import scan

class SpectrumController(Controller):
    spectrum: AttrR[np.ndarray] = AttrR(Waveform(np.float64, shape=(1000,)))

    @scan(period=0.1)
    async def read_spectrum(self):
        # Get data from device (e.g., numpy array)
        data = await self.device.get_spectrum()

        # Update the attribute
        await self.spectrum.update(data)
```

### Shape Validation

Waveforms validate that data fits within the declared shape:

```python
wave = Waveform(np.float64, shape=(100,))

# OK - fits within shape
wave.validate(np.array([1.0, 2.0, 3.0]))

# Error - exceeds maximum shape
wave.validate(np.arange(200))  # ValueError: shape (200,) exceeds maximum (100,)
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
    results: AttrR[np.ndarray] = AttrR(Table([
        ("name", "S32"),       # 32-character string
        ("value", np.float64),
        ("valid", np.bool_),
    ]))
```

### Table Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `structured_dtype` | `list[tuple[str, DTypeLike]]` | List of (name, dtype) tuples |

### Creating Table Data

```python
from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Table

class ChannelController(Controller):
    channel_data: AttrR[np.ndarray] = AttrR(Table([
        ("channel", np.int32),
        ("temperature", np.float64),
        ("status", "S10"),
    ]))

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
table = controller.results.get()

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
