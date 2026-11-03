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

## FastCS Initialisation

Specific `Controller` classes can optionally implement an async `initialise` method to
perform any start up logic. The intention here is that the `__init__` method should be
minimal and the `initialise` method performs any long running calls, such as querying an
API, allowing FastCS to run these concurrently to reduce start times.

Take the driver implementation from the previous tutorial and remove the
statically defined `Attributes` and creation of sub controllers in `__init__`. Then
implement an `initialise` method to create these dynamically instead.

Create a pydantic model to validate the response from the device

:::{literalinclude} /snippets/dynamic.py
:lines: 5,18-35
:::

Create a function to parse the dictionary, validate the entries against the model and
create `Attributes`.

:::{literalinclude} /snippets/dynamic.py
:lines: 38-56
:::

Update the controllers to not define attributes statically and implement initialise
methods to create these attributes dynamically.

:::{literalinclude} /snippets/dynamic.py
:lines: 91-131
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
