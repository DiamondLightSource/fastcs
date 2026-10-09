# Update Attribute Values from a Device

There are different patterns for pushing values from a device into attributes to suit
different use cases. Choose the pattern that fits how the device API delivers data.

## Update Tasks via `AttributeIO.update`

Use this pattern when each attribute maps to an independent request to the device. The
`AttributeIO.update` method is called periodically as a background task, once per
attribute, at the rate set by `update_period` in the attribute's `AttributeIORef`.

Define an `AttributeIORef` with an `update_period` and implement `AttributeIO.update`
to query the device and call `attr.update` with the result:

```python
from dataclasses import KW_ONLY, dataclass

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import Float, String


@dataclass
class MyDeviceIORef(AttributeIORef):
    register: str
    _: KW_ONLY
    update_period: float | None = 0.5


class MyDeviceIO(AttributeIO[float, MyDeviceIORef]):
    def __init__(self, connection):
        super().__init__()
        self._connection = connection

    async def update(self, attr: AttrR[float, MyDeviceIORef]):
        response = await self._connection.send_query(f"{attr.io_ref.register}?\r\n")
        await attr.update(float(response.strip()))

    async def send(self, attr: AttrW[float, MyDeviceIORef], value: float):
        await self._connection.send_command(f"{attr.io_ref.register}={value}\r\n")


class MyController(Controller):
    temperature = AttrR(Float(), io_ref=MyDeviceIORef("T"))
    setpoint = AttrRW(Float(), io_ref=MyDeviceIORef("S", update_period=1.0))
    label = AttrR(String(), io_ref=MyDeviceIORef("L", update_period=None))

    def __init__(self, connection):
        super().__init__(ios=[MyDeviceIO(connection)])
```

Setting `update_period` to:

- A positive `float` — polls at that interval in seconds.
- `None` — no automatic updates; the attribute value is only set explicitly (e.g. from a
  scan method or subscription callback).
- `ONCE` (imported from `fastcs`) — called once on startup and not again.

## Initial Read with Event-Driven Updates from Puts

Use this pattern when attributes need their initial value read on startup, but subsequent
updates arrive as side-effects of write operations rather than on a fixed poll cycle.
This is common for devices that echo back related parameter values in their response to a
set command.

Set `update_period=ONCE` on the `AttributeIORef` so that `AttributeIO.update` is called
once when the application starts. Then, in `AttributeIO.send`, parse the device's
response to the put and call `attr.update` on any attributes whose values have changed:

```python
from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass

from fastcs import ONCE
from fastcs.attributes import AttributeIO, AttributeIORef, AttrR, AttrRW, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import Float


@dataclass
class MyDeviceIORef(AttributeIORef):
    register: str
    _: KW_ONLY
    update_period: float | None = ONCE



PutResponseCallback = Callable[[str], Awaitable[None]]


class MyDeviceIO(AttributeIO[float, MyDeviceIORef]):
    def __init__(self, connection, on_put_response: PutResponseCallback | None = None):
        super().__init__()
        self._connection = connection
        self._on_put_response = on_put_response

    async def update(self, attr: AttrR[float, MyDeviceIORef]):
        response = await self._connection.send_query(f"{attr.io_ref.register}?\r\n")
        await attr.update(float(response.strip()))

    async def send(self, attr: AttrW[float, MyDeviceIORef], value: float):
        # Device responds with a snapshot of all current values after a set
        response = await self._connection.send_query(
            f"{attr.io_ref.register}={value}\r\n"
        )
        if self._on_put_response is not None:
            await self._on_put_response(response)


class MyController(Controller):
    setpoint = AttrRW(Float(), io_ref=MyDeviceIORef("S"))
    actual_temperature = AttrR(Float(), io_ref=MyDeviceIORef("T"))
    power = AttrR(Float(), io_ref=MyDeviceIORef("P"))
    status = AttrR(Float(), io_ref=MyDeviceIORef("X"))

    def __init__(self, connection):
        super().__init__(ios=[MyDeviceIO(connection, self._handle_put_response)])

    async def _handle_put_response(self, response: str) -> None:
        actual, power, status = response.strip().split(",")
        await self.actual_temperature.update(float(actual))
        await self.power.update(float(power))
        await self.status.update(float(status))
```

Attributes that are updated as side-effects of puts can still carry `update_period=ONCE`
so they also get their initial value on startup. Set `update_period=None` instead if the
device response to the put is the only source of truth and no initial poll is needed.

## Batched Updates via a Scan Method

Use this pattern when the device returns values for multiple attributes in a single
response. A `@scan` method runs periodically on the controller and distributes the
results by calling `attr.update` directly on each attribute.

Attributes that are updated this way do not need an `io_ref` with an `update_period`
because the scan method drives the updates rather than individual IO tasks.

```python
import json

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Float
from fastcs.methods import scan


class ChannelController(Controller):
    voltage = AttrR(Float())  # No io_ref — updated by parent scan method

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

### Scan as a cache for `AttributeIO.update`

When there are many attributes to update from a batched response, calling `attr.update`
for each one inside the scan method becomes verbose. Instead, the scan can populate a
cache on the `AttributeIO`, and each attribute's regular update task reads from that
cache rather than querying the device while the device is still only queried once per
cycle.

```python
import json
from dataclasses import KW_ONLY, dataclass

from fastcs.attributes import AttributeIO, AttributeIORef, AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Float
from fastcs.methods import scan


@dataclass
class ChannelIORef(AttributeIORef):
    index: int
    _: KW_ONLY
    update_period: float | None = 0.1


class ChannelIO(AttributeIO[float, ChannelIORef]):
    def __init__(self):
        super().__init__()
        self._cache: dict[int, float] = {}

    def update_cache(self, values: dict[int, float]) -> None:
        self._cache = values

    async def update(self, attr: AttrR[float, ChannelIORef]):
        cached = self._cache.get(attr.io_ref.index)
        if cached is not None:
            await attr.update(cached)


class ChannelController(Controller):
    def __init__(self, index: int, io: ChannelIO):
        super().__init__(f"Ch{index:02d}", ios=[io])
        self.voltage = AttrR(Float(), io_ref=ChannelIORef(index))


class MultiChannelController(Controller):
    def __init__(self, channel_count: int, connection):
        self._connection = connection
        self._channel_io = ChannelIO()
        super().__init__()

        for i in range(channel_count):
            self.add_sub_controller(f"Ch{i:02d}", ChannelController(i, self._channel_io))

    @scan(0.1)
    async def fetch_voltages(self):
        voltages = json.loads(
            (await self._connection.send_query("V?\r\n")).strip()
        )
        self._channel_io.update_cache(dict(enumerate(map(float, voltages))))
```

## Subscription Callbacks

Use this pattern when the device library (or protocol) delivers value changes by calling
a user-supplied callback rather than responding to polls. Wrap `attr.update` in an async
callback and register it with the library.

```python
import asyncio

from fastcs.attributes import AttrR
from fastcs.controllers import Controller
from fastcs.datatypes import Float


class SubscriptionController(Controller):
    temperature = AttrR(Float())

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
