# Dynamic FastCS drivers

## Demo Simulation

The demo simulation used in the previous tutorial has a command `API?` to list all of its
commands. This allows you to introspect the device and create the API dynamically,
instead of defining all the attributes statically. The response will look like this

```
{
    "Device ID": {"command": "ID", "type": "str", "access_mode": "r"},
    "Power": {"command": "P", "type": "float", "access_mode": "rw"},
    "Ramp Rate": {"command": "R", "type": "float", "access_mode": "rw"},
    "Ramps": [
        {
            "Start": {"command": "S01", "type": "int", "access_mode": "rw"},
            "End": {"command": "E01", "type": "int", "access_mode": "rw"},
            "Enabled": {"command": "N01", "type": "int", "access_mode": "rw"},
            "Target": {"command": "T01", "type": "float", "access_mode": "rw"},
            "Actual": {"command": "A01", "type": "float", "access_mode": "rw"},
        },
        ...,
    ],
}
```

This contains all the metadata about the parameters in the API needed to create the
`Attributes` from the previous tutorial. For a real device, this might also include
fields such as the units of numerical parameters, limits that a parameter can be set to,
or a description for the parameter.

## Introspection

A `Connection` can describe the device it connects to by implementing `introspect`.
FastCS opens every connection and calls `introspect` before constructing any
controller, and stores what it returned as `connection.introspection`, so a controller
can use it in its `__init__`.

Take the driver implementation from the previous tutorial and remove the statically
defined `Attributes` and creation of sub controllers in `__init__`. Then create them
from the introspection instead.

Create a pydantic model to validate the response from the device

:::{literalinclude} /snippets/dynamic.py
:lines: 5,22-39
:::

Create a function to parse the dictionary, validate the entries against the model and
create `Attributes`.

:::{literalinclude} /snippets/dynamic.py
:lines: 42-60
:::

Create a connection whose `introspect` queries the `API?` command and returns the
parsed response. The type parameter, `IPConnection[dict[str, Any]]`, is the type that
`introspect` returns.

:::{literalinclude} /snippets/dynamic.py
:lines: 63-66
:::

Update the controllers to create their attributes from `connection.introspection` in
`__init__`, instead of defining them statically.

:::{literalinclude} /snippets/dynamic.py
:lines: 101-127
:::

The `suffix` field should also be removed from `TemperatureController` and
`TemperatureRampController` and then not used in `TemperatureControllerAttributeIO`
because the `command` field on `TemperatureControllerParameter` includes this.

TODO: Add `enabled` back in to `TemperatureRampController` and recreate `disable_all` to
demonstrate validation of introspected Attributes.

The full code is as follows

::::{admonition} Code
:class: dropdown, hint

:::{literalinclude} /snippets/dynamic.py
:::

::::
