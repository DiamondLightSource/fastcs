# Give a Command Arguments and a Return Value

A `@command` may take positional arguments and give a value back. Both are
declared the ordinary way - by annotating the method - and both are optional and
independent, so a command can take arguments and return nothing, return
something and take nothing, or do both.

```python
from fastcs.controllers import Controller
from fastcs.methods import command

class Stage(Controller):
    @command()
    async def stop(self) -> None:
        """Void: no arguments, no return value."""
        await self._protocol.stop()

    @command()
    async def move_to(self, position: float, wait: bool) -> None:
        """Two positional arguments."""
        await self._protocol.move(position, wait)

    @command()
    async def measure(self) -> float:
        """A return value."""
        return await self._protocol.read_position()
```

## What a command may take and return

Arguments and return values are `bool`, `int`, `float`, `str`, or an
`enum.Enum` subclass - the same python types an attribute holds, minus arrays
and tables. Everything must be annotated: a command's signature is what
transports read to decide how to expose it, so it has to be fully known.

```python
@command()
async def move_to(self, position):     # TypeError: no type annotation
    ...

@command()
async def plot(self, trace: list[float]):  # TypeError: unsupported type
    ...
```

Arguments are positional. Keyword-only arguments, `*args` and `**kwargs` are
rejected.

An array-valued command has an attribute-shaped alternative: write the array to
an `AttrW` and trigger a void command, rather than passing it as an argument.

## Which transports serve them

Not every protocol can carry a typed call, so each transport declares what it
can do rather than the framework assuming they are all alike. A command a
transport cannot serve is **skipped with a warning at start-up** - the rest of
the controller is still served.

| Transport | Void command | Arguments | Return value |
| --------- | ------------ | --------- | ------------ |
| REST      | ✅            | ✅ any number, as a JSON body | ✅ as `{"value": …}` |
| GraphQL   | ✅            | ✅ any number, as mutation arguments | ✅ the mutation result |
| Tango     | ✅            | ⚠️ at most one, and not an enum | ⚠️ not an enum |
| EPICS CA  | ✅            | ❌         | ❌            |
| EPICS PVA | ✅            | ❌         | ❌            |

The EPICS transports serve a command as a single "do it" PV. There is no PV
representation of "call with these arguments and give me this back" that is not
already a set of attributes, so a typed command has nothing to map onto. Tango
commands carry at most one input value, which is a limit of the protocol.

If a command must be reachable over EPICS, keep it void and put its arguments
and results on attributes:

```python
class Stage(Controller):
    target: AttrRW[float]
    last_position: AttrR[float]

    @command()
    async def move(self) -> None:
        await self._protocol.move(self.target.setpoint)
        await self.last_position.update(await self._protocol.read_position())
```

## Reading a command's signature

A transport - or anything else walking a `ControllerAPI` - gets the whole
picture from the command itself:

```python
command = controller_api.command_methods["move_to"]

command.signature          # (position: float, wait: bool) -> None
command.argument_types     # (float, bool)
command.return_datatype    # None
command.is_void            # False
```

`signature` is the bound signature, without `self`, so it is what a caller
would actually pass.
