# Update Attribute Values from a Device

There are different patterns for pushing values from a device into attributes to suit
different use cases. Choose the pattern that fits how the device API delivers data.

## Poll via a Getter

Use this pattern when each attribute maps to an independent request to the device. Give
the attribute a `getter` wrapped in `Polled` and FastCS will call it periodically as a
background task, at the period given.

Write a getter that queries the device and returns the value - the framework caches it
and calls any update callbacks; there's no need to call `attr.update` yourself:

```python
from fastcs.attributes import AttrR, AttrRW, NotPolled, Polled
from fastcs.controllers import Controller


class MyController(Controller):
    def __init__(self, connection):
        self._connection = connection
        super().__init__()

        self.temperature = AttrR(
            float, getter=Polled(self._get_temperature, period=0.5)
        )
        self.setpoint = AttrRW(
            float,
            getter=Polled(self._get_setpoint, period=1.0),
            setter=self._set_setpoint,
        )
        self.label = AttrR(str, getter=NotPolled(self._get_label))

    async def _get_temperature(self) -> float:
        response = await self._connection.send_query("T?\r\n")
        return float(response.strip())

    async def _get_setpoint(self) -> float:
        response = await self._connection.send_query("S?\r\n")
        return float(response.strip())

    async def _set_setpoint(self, value: float) -> None:
        await self._connection.send_command(f"S={value}\r\n")

    async def _get_label(self) -> str:
        response = await self._connection.send_query("L?\r\n")
        return response.strip()
```

How the getter is passed decides when it is called:

- A bare getter (`getter=self._get_label`) — the `ONCE` schedule: read when the
  controller connects, and not again. Use it for values that only change because
  you changed them, such as writable configuration the device holds for you.
- `Polled(getter, period=0.5)` — polls at that interval in seconds. Use it for
  values the device changes on its own, such as readings and status.
- `NotPolled(getter)` — never read on a schedule; the attribute value is only set
  explicitly (e.g. from a scan method or subscription callback), or read on demand
  via `await attr.poll()`. This differs from giving no getter at all, which leaves
  nothing to read on demand.

`ONCE` is the default when a getter is given, so polling is opted into per
attribute rather than being something you have to remember to switch off.

## Initial Read with Event-Driven Updates from Sets

Use this pattern when attributes need their initial value read on startup, but
subsequent updates arrive as side-effects of write operations rather than on a fixed
poll cycle. This is common for devices that echo back related parameter values in their
response to a set command.

Pass the getter bare, without `Polled`, so it runs once on startup and not again.
Then, in the setter, parse the device's response and call `.update()` directly on any
sibling attributes whose values have changed:

```python
from fastcs.attributes import AttrR, AttrRW
from fastcs.controllers import Controller


class MyController(Controller):
    def __init__(self, connection):
        self._connection = connection
        super().__init__()

        self.setpoint = AttrRW(
            float, getter=self._get_setpoint, setter=self._set_setpoint
        )
        self.actual_temperature = AttrR(float, getter=self._get_actual_temperature)
        self.power = AttrR(float, getter=self._get_power)
        self.status = AttrR(float, getter=self._get_status)

    async def _get_setpoint(self) -> float:
        return float((await self._connection.send_query("S?\r\n")).strip())

    async def _set_setpoint(self, value: float) -> None:
        # Device responds with a snapshot of all current values after a set
        response = await self._connection.send_query(f"S={value}\r\n")
        actual, power, status = response.strip().split(",")
        await self.actual_temperature.update(float(actual))
        await self.power.update(float(power))
        await self.status.update(float(status))

    async def _get_actual_temperature(self) -> float:
        return float((await self._connection.send_query("T?\r\n")).strip())

    async def _get_power(self) -> float:
        return float((await self._connection.send_query("P?\r\n")).strip())

    async def _get_status(self) -> float:
        return float((await self._connection.send_query("X?\r\n")).strip())
```

Attributes that are updated as a side-effect of a set can still take a bare getter,
so they also get their initial value on startup. Use `NotPolled(getter)` instead if
the device's response to the set is the only source of truth and no initial poll is
needed.

## Batched Updates via a Scan Method

Use this pattern when the device returns values for multiple attributes in a single
response. A `@scan` method runs periodically on the controller and distributes the
results by calling `attr.update` directly on each attribute.

Attributes that are updated this way do not need a `getter` at all, because
the scan method drives the updates directly, rather than each attribute polling
independently.

```python
import json

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.methods import scan


class ChannelController(Controller):
    voltage: AttrR[float]  # No getter — updated by parent scan method

    def __init__(self, index: int, connection):
        super().__init__(f"Ch{index:02d}")
        self._index = index
        self._connection = connection


class MultiChannelController(Controller):
    def __init__(self, channel_count: int, connection):
        self._connection = connection
        super().__init__()

        self._channels: list[ChannelController] = []
        for i in range(channel_count):
            ch = ChannelController(i, connection)
            self._channels.append(ch)
            self.add_sub_controller(f"Ch{i:02d}", ch)

    @scan(0.1)
    async def update_voltages(self):
        # One request returns all channel voltages
        voltages = json.loads(
            (await self._connection.send_query("V?\r\n")).strip()
        )
        for channel, voltage in zip(self._channels, voltages):
            await channel.voltage.update(float(voltage))
```

The scan period (here `0.1` seconds) sets how often the batched query runs. Scans that
raise an exception will pause and wait for `reconnect()` to be called before resuming.

### Scan as a cache for getters

When there are many attributes to update from a batched response, calling `attr.update`
for each one inside the scan method becomes verbose. Instead, the scan can populate a
shared cache, and each attribute's own getter (polled independently) reads from that
cache rather than querying the device - the device is still only queried once per cycle.

```python
import json

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.methods import scan


class ChannelController(Controller):
    def __init__(self, index: int, cache: dict[int, float]):
        self._index = index
        self._cache = cache
        super().__init__(f"Ch{index:02d}")

        self.voltage = AttrR(float, getter=Polled(self._get_voltage, period=0.1))

    async def _get_voltage(self) -> float:
        return self._cache.get(self._index, 0.0)


class MultiChannelController(Controller):
    def __init__(self, channel_count: int, connection):
        self._connection = connection
        self._cache: dict[int, float] = {}
        super().__init__()

        self._channels: list[ChannelController] = []
        for i in range(channel_count):
            ch = ChannelController(i, self._cache)
            self._channels.append(ch)
            self.add_sub_controller(f"Ch{i:02d}", ch)

    @scan(0.1)
    async def fetch_voltages(self):
        voltages = json.loads(
            (await self._connection.send_query("V?\r\n")).strip()
        )
        self._cache.clear()
        self._cache.update(enumerate(map(float, voltages)))
```

## Subscription Callbacks

Use this pattern when the device library (or protocol) delivers value changes by calling
a user-supplied callback rather than responding to polls. Wrap `attr.update` in an async
callback and register it with the library.

```python
import asyncio

from fastcs.attributes import AttrR
from fastcs.controllers import Controller


class SubscriptionController(Controller):
    temperature: AttrR[float]

    def __init__(self, subscription_client):
        super().__init__()
        self._client = subscription_client

    async def connect(self):
        # Register an async callback that forwards updates into the attribute.
        async def on_temperature_change(value: float) -> None:
            await self.temperature.update(value)

        await self._client.subscribe("temperature", on_temperature_change)
        await super().connect()
```

If the library only supports synchronous callbacks, schedule the coroutine onto the
running event loop:

```python
def on_temperature_change_sync(value: float) -> None:
    asyncio.get_event_loop().call_soon_threadsafe(
        asyncio.ensure_future, self.temperature.update(value)
    )

self._client.subscribe("temperature", on_temperature_change_sync)
```
