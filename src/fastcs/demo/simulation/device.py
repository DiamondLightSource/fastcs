import json
import logging
import traceback
from os import _exit
from random import uniform

import numpy as np
import numpy.typing as npt
import pydantic.dataclasses
from tickit.adapters.io import TcpIo
from tickit.adapters.specifications.regex_command import RegexCommand
from tickit.adapters.tcp import CommandAdapter
from tickit.core.adapter import AdapterContainer
from tickit.core.components.component import Component, ComponentConfig
from tickit.core.components.device_component import DeviceComponent
from tickit.core.device import Device, DeviceUpdate
from tickit.core.typedefs import SimTime
from tickit.utils.byte_format import ByteFormat
from typing_extensions import TypedDict

LOGGER = logging.getLogger(__name__)


def handle_exceptions(func):
    """Log and exit if the wrapped function raises an Exception.

    Tickit does not properly handle exceptions in e.g. update() methods. This wrapper is
    used to ensure we see the errors as soon as they occur.
    """

    def wrapped_func(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:  # noqa
            logging.error(traceback.format_exc())
            _exit(-1)

    return wrapped_func


class TempControllerDevice(Device):
    class Inputs(TypedDict):
        flux: float

    class Outputs(TypedDict):
        flux: float

    def __init__(
        self,
        num_ramp_controllers: int,
        default_start: float,
        default_end: float,
    ) -> None:
        self._num = num_ramp_controllers
        self._start = np.full(num_ramp_controllers, default_start, dtype=int)
        self._end = np.full(num_ramp_controllers, default_end, dtype=int)
        self._target = np.zeros(num_ramp_controllers, dtype=float)
        self._actual = np.zeros(num_ramp_controllers, dtype=float)
        self._enabled = np.full(num_ramp_controllers, 0, dtype=int)
        self._start_times = np.full(num_ramp_controllers, -1, dtype=int)

        self._ramp_rate: float = 1  # 1 unit/s

        self.initialised = False

    def get_target(self, index: int):
        return self._target[index]

    def get_actual(self, index: int):
        return self._actual[index]

    def get_start(self, index: int):
        return self._start[index]

    def set_start(self, index: int, value: int):
        self._start[index] = value
        LOGGER.info(f"Set ramp {index} start to {value}")

    def get_end(self, index: int):
        return self._end[index]

    def set_end(self, index: int, value: int):
        self._end[index] = value
        LOGGER.info(f"Set ramp {index} end to {value}")

    def set_enabled(self, index: int, value: int):
        if value:
            self._target[index] = float(self._start[index])
            self._start_times[index] = -1
            self._enabled[index] = 1
            LOGGER.info(f"Started ramp {index}")
        else:
            self._enabled[index] = 0
            LOGGER.info(f"Stopped ramp {index}")

    def get_enabled(self, index: int):
        return self._enabled[index]

    def get_ramp_rate(self):
        return self._ramp_rate

    def set_ramp_rate(self, rate: float):
        self._ramp_rate = rate
        LOGGER.info(f"Set ramp rate to {rate}")

    def get_voltages(self):
        return [self.get_actual(i) / 17 for i in range(0, self._num)]

    def get_power(self):
        return sum(self.get_voltages() * 50)

    def ramp(
        self,
        periods: npt.NDArray,
    ):
        can_ramp = np.logical_and(self._enabled, self._target < self._end)
        max_temps = self._target.copy()
        max_temps[can_ramp] += self._ramp_rate * (periods[can_ramp] / 1e9)
        self._target = np.minimum(max_temps, self._end)
        self._enabled[self._target >= self._end] = 0

        self._actual = [
            float(self.get_target(i) + uniform(-0.5, 0.5)) if self.get_enabled(i) else 0
            for i in range(0, self._num)
        ]

    @handle_exceptions
    def update(self, time: SimTime, inputs: Inputs) -> DeviceUpdate[Outputs]:
        if not self.initialised:
            LOGGER.info("Temperature controller running")
            self.initialised = True

        self._start_times[self._start_times == -1] = int(time)
        periods = np.full(self._num, int(time)) - self._start_times
        self.ramp(periods)
        self._start_times = np.full(self._num, int(time))

        LOGGER.info(
            f"Target Temperatures: {', '.join(f'{float(t):.3f}' for t in self._target)}"
        )
        LOGGER.info(
            f"Actual Temperatures: {', '.join(f'{float(t):.3f}' for t in self._actual)}"
        )

        call_at = SimTime(int(time) + int(1e8)) if np.any(self._enabled) else None
        return DeviceUpdate(TempControllerDevice.Outputs(flux=inputs["flux"]), call_at)

    def get_commands(self):
        return json.dumps(
            {
                "Device ID": {"command": "ID", "type": "str", "access_mode": "r"},
                "Power": {"command": "P", "type": "float", "access_mode": "rw"},
                "Ramp Rate": {"command": "R", "type": "float", "access_mode": "rw"},
                "Ramps": [
                    {
                        "Start": {
                            "command": f"S{idx:02d}",
                            "type": "int",
                            "access_mode": "rw",
                        },
                        "End": {
                            "command": f"E{idx:02d}",
                            "type": "int",
                            "access_mode": "rw",
                        },
                        "Enabled": {
                            "command": f"N{idx:02d}",
                            "type": "int",
                            "access_mode": "rw",
                        },
                        "Target": {
                            "command": f"T{idx:02d}",
                            "type": "float",
                            "access_mode": "rw",
                        },
                        "Actual": {
                            "command": f"A{idx:02d}",
                            "type": "float",
                            "access_mode": "rw",
                        },
                    }
                    for idx in range(1, self._num + 1)
                ],
            }
        )


class TempControllerAdapter(CommandAdapter):
    device: TempControllerDevice
    _byte_format: ByteFormat = ByteFormat(b"%b\r\n")

    def __init__(self, device: TempControllerDevice) -> None:
        super().__init__()
        self.device = device

    def _validate_index(self, index: str) -> int:
        int_index = int(index) - 1
        assert int_index >= 0, "TempController Indices start at 01"
        return int_index

    @RegexCommand(r"ID\?", False, "utf-8")
    async def get_id(self) -> bytes:
        return b"SIMTCONT123"

    @RegexCommand(r"T([0-9][0-9])\?", False, "utf-8")
    async def get_target(self, index: str) -> bytes:
        int_index = self._validate_index(index)
        return str(self.device.get_target(int_index)).encode("utf-8")

    @RegexCommand(r"A([0-9][0-9])\?", False, "utf-8")
    async def get_actual(self, index: str) -> bytes:
        int_index = self._validate_index(index)
        return str(self.device.get_actual(int_index)).encode("utf-8")

    @RegexCommand(r"N([0-9][0-9])\?", False, "utf-8")
    async def get_enabled(self, index: str) -> bytes:
        int_index = self._validate_index(index)
        return str(self.device.get_enabled(int_index)).encode("utf-8")

    @RegexCommand(r"N([0-9][0-9])=([01])", True, "utf-8")
    async def set_enabled(self, index: str, value: int) -> None:
        int_index = self._validate_index(index)
        self.device.set_enabled(int_index, value)

    @RegexCommand(r"S([0-9][0-9])\?", False, "utf-8")
    async def get_start(self, index: str) -> bytes:
        int_index = self._validate_index(index)
        return str(self.device.get_start(int_index)).encode("utf-8")

    @RegexCommand(r"S([0-9][0-9])=(\d+\.?\d*)", True, "utf-8")
    async def set_start(self, index: str, value: str) -> None:
        int_index = self._validate_index(index)
        self.device.set_start(int_index, int(value))

    @RegexCommand(r"E([0-9][0-9])\?", False, "utf-8")
    async def get_end(self, index: str) -> bytes:
        int_index = self._validate_index(index)
        return str(self.device.get_end(int_index)).encode("utf-8")

    @RegexCommand(r"E([0-9][0-9])=(\d+\.?\d*)", True, "utf-8")
    async def set_end(self, index: str, value: str) -> None:
        int_index = self._validate_index(index)
        self.device.set_end(int_index, int(value))

    @RegexCommand(r"R\?", False, "utf-8")
    async def get_ramp_rate(self) -> bytes:
        return str(self.device.get_ramp_rate()).encode("utf-8")

    @RegexCommand(r"R=(\d+\.?\d*)", True, "utf-8")
    async def set_ramp_rate(self, value: str) -> None:
        self.device.set_ramp_rate(float(value))

    @RegexCommand(r"V\?", False, "utf-8")
    async def get_voltages(self) -> bytes:
        return str(self.device.get_voltages()).encode("utf-8")

    @RegexCommand(r"P\?", False, "utf-8")
    async def get_power(self) -> bytes:
        return str(self.device.get_power()).encode("utf-8")

    @RegexCommand(r"API\?", False, "utf-8")
    async def get_commands(self) -> bytes:
        return str(self.device.get_commands()).encode("utf-8")

    @RegexCommand(r"\w*", False, "utf-8")
    async def ignore_whitespace(self) -> None:
        pass


# https://github.com/DiamondLightSource/tickit/issues/212
@pydantic.dataclasses.dataclass
class TempController(ComponentConfig):  # type: ignore
    num_ramp_controllers: int
    default_start: float = 0
    default_end: float = 50
    host: str = "localhost"
    port: int = 25565

    def __call__(self) -> Component:
        device = TempControllerDevice(
            num_ramp_controllers=self.num_ramp_controllers,
            default_start=self.default_start,
            default_end=self.default_end,
        )
        return DeviceComponent(
            name=self.name,
            device=device,
            adapters=[
                AdapterContainer(
                    TempControllerAdapter(device),
                    TcpIo(self.host, self.port),
                )
            ],
        )
