"""A cut-down, Eiger-shaped fake REST device for the introspectable controller demo.

Mimics the shape of a real Eiger detector's parameter-tree REST API (subsystems of
named parameters, a ``keys`` listing endpoint, per-parameter GET/PUT) without any of
the real detector logic. Introspection earns its complexity only when a device's
parameters aren't knowable at author time - this sim exists to give that a genuine,
self-describing backend to introspect.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, HTTPException

ValueType = Literal["float", "int", "string", "bool"]
AccessMode = Literal["r", "rw"]
Subsystem = Literal["config", "status"]

API_PREFIX = "/detector/api/1.8.0"

# The sim flips its temperature between these two values so the front end has
# something visibly changing to poll.
TEMPERATURES = (20.0, 30.0)


@dataclass
class EigerParameter:
    value: Any
    value_type: ValueType
    access_mode: AccessMode = "r"
    allowed_values: list[str] | None = None
    """The permitted values of a discrete parameter, as the real detector reports them.

    Only discrete parameters carry this, and it is the metadata a client needs to
    introspect the parameter as an enum rather than a bare string.
    """


def _initial_state() -> dict[Subsystem, dict[str, EigerParameter]]:
    return {
        "config": {
            "count_time": EigerParameter(0.1, "float", "rw"),
            "frame_time": EigerParameter(0.1, "float", "rw"),
            "nimages": EigerParameter(1, "int", "rw"),
            "description": EigerParameter("Simulated Eiger", "string", "r"),
        },
        "status": {
            "state": EigerParameter(
                "idle", "string", "r", allowed_values=["idle", "ready", "acquire"]
            ),
            "temperature": EigerParameter(22.5, "float", "r"),
            "humidity": EigerParameter(32.1, "float", "r"),
        },
    }


async def _oscillate_temperature(
    parameter: EigerParameter, period: float = 0.5
) -> None:
    """Flip a temperature parameter between two known values forever.

    Runs as a background task under the app's lifespan (started by a real server,
    e.g. uvicorn). The in-process ASGI transport used by the controller in tests
    does not start lifespan events, so a test that wants the task running drives
    the lifespan explicitly.
    """
    index = 0
    while True:
        await asyncio.sleep(period)
        index = 1 - index
        parameter.value = TEMPERATURES[index]


def create_eiger_sim_app() -> FastAPI:
    """Create a FastAPI app simulating a cut-down Eiger detector REST API."""
    state = _initial_state()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(
            _oscillate_temperature(state["status"]["temperature"])
        )
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(lifespan=lifespan)
    # Backdoor: expose the parameter tree so tests can set read-only values (e.g.
    # ``state``, which has no PUT route) and then poll them through the controller.
    app.state.sim = state

    def _subsystem(subsystem: str) -> dict[str, EigerParameter]:
        try:
            return state[subsystem]  # type: ignore[index]
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Unknown subsystem '{subsystem}'"
            ) from None

    def _parameter(subsystem: str, param: str) -> EigerParameter:
        try:
            return _subsystem(subsystem)[param]
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Unknown parameter '{param}'"
            ) from None

    @app.get(API_PREFIX + "/{subsystem}/keys")
    async def get_keys(subsystem: str) -> list[str]:
        return list(_subsystem(subsystem))

    @app.get(API_PREFIX + "/{subsystem}/{param}")
    async def get_parameter(subsystem: str, param: str) -> dict[str, Any]:
        parameter = _parameter(subsystem, param)
        data: dict[str, Any] = {
            "value": parameter.value,
            "value_type": parameter.value_type,
            "access_mode": parameter.access_mode,
        }
        # Only discrete parameters report their options, as on the real detector.
        if parameter.allowed_values is not None:
            data["allowed_values"] = parameter.allowed_values
        return data

    @app.put(API_PREFIX + "/{subsystem}/{param}")
    async def put_parameter(
        subsystem: str, param: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        parameter = _parameter(subsystem, param)
        if parameter.access_mode != "rw":
            raise HTTPException(
                status_code=403, detail=f"Parameter '{param}' is read-only"
            )
        parameter.value = body["value"]
        return {"value": parameter.value}

    return app
