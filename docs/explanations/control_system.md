# The FastCS class

`FastCS` is the entrypoint for a fastcs application. It connects a `Controller` to one
or more `Transport`s, runs the controller's update loops as background tasks, and
manages the full application lifecycle from startup to shutdown.

## Construction

```python
from fastcs import FastCS
from fastcs.controllers import Controller
from fastcs.transports.epics import EpicsCATransport


class MyController(Controller):
    pass


control_system = FastCS(controller=MyController(), transports=[EpicsCATransport()])

control_system.run()
```

## Startup and Runtime

Calling `control_system.run()` (or `await control_system.serve()`) executes the
following steps in order:

1. **Initialise the controller:** `controller.initialise()` is awaited, allowing the
   controller to query the device and dynamically add attributes before the API is
   built. After that, `controller.post_initialise()` is called to perform any final
   setup, such as validating all type hints are satisfied.

2. **Build the API:** `controller.create_api_and_tasks()` returns the `ControllerAPI`
   that transports will use, plus two lists of coroutines: *initial tasks* (run once at
   startup) and *scan tasks* (run as continuous background loops).

3. **Connect transports:** each transport's `connect()` method is called with the
   `ControllerAPI`. This lets the transport inspect the controller's attributes and
   commands to prepare its protocol-specific representations before serving begins.

4. **Connect the controller:** `controller.connect()` is called to open the connection
   to the device and perform any other setup logic.

5. **Run initial tasks:** each initial-task coroutine is awaited in sequence. These are
   `@scan(period=ONCE)` methods and `AttributeIO` update callbacks with
   `update_period=ONCE`.

6. **Start scan tasks:** each scan task coroutine is wrapped in an `asyncio.Task` and
   run as a background task for the lifetime of the application.

7. **Gather transport coroutines:** `asyncio.gather` runs all transport `serve()`
   coroutines concurrently. Each transport begins accepting and responding to protocol
   requests.

8. **Scan pause and reconnect**: If any scan tasks raise exceptions, all scan tasks are
   paused until `reconnect`.

## The Interactive Shell

Alongside the transport coroutines, FastCS launches an embedded
[IPython](https://ipython.org/) shell (unless `interactive=False` is passed). The shell
namespace is pre-populated with:

| Name | Value |
|------|-------|
| `controller` | The root controller instance |
| `transports` | The class names of active transports |
| `run` | A helper that schedules a coroutine on the FastCS event loop from the IPython thread |
| *transport-specific keys* | Any entries exposed via each transport's `context` property |

The shell runs in a separate thread so it does not block the asyncio event loop. When
the user exits the shell, the application begins its shutdown sequence.

When `interactive=False` a simple coroutine that blocks forever keeps the application
alive until the task is cancelled externally (e.g. SIGINT).

## Shutdown Sequence

When then application is stopped, FastCS performs an orderly teardown:

1. **Cancel scan tasks:** each background scan task is cancelled and removed, stopping
   all periodic polling.

2. **Disconnect the controller:** `controller.disconnect()` is awaited, allowing the
   controller to release device resources cleanly.
