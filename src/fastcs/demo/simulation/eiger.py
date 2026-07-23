"""A cut-down, Eiger-shaped fake REST device for the introspectable controller demo.

Mimics the shape of a real Eiger detector's parameter-tree REST API (subsystems of
named parameters, a ``keys`` listing endpoint, per-parameter GET/PUT) without any of
the real detector logic. Introspection earns its complexity only when a device's
parameters aren't knowable at author time - this sim exists to give that a genuine,
self-describing backend to introspect.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, HTTPException

ValueType = Literal["float", "int", "string", "bool"]
AccessMode = Literal["r", "rw"]
Subsystem = Literal["config", "status"]

API_PREFIX = "/detector/api/1.8.0"


@dataclass
class EigerParameter:
    value: Any
    value_type: ValueType
    access_mode: AccessMode = "r"


def _initial_state() -> dict[Subsystem, dict[str, EigerParameter]]:
    return {
        "config": {
            "count_time": EigerParameter(0.1, "float", "rw"),
            "frame_time": EigerParameter(0.1, "float", "rw"),
            "nimages": EigerParameter(1, "int", "rw"),
            "description": EigerParameter("Simulated Eiger", "string", "r"),
        },
        "status": {
            "state": EigerParameter("idle", "string", "r"),
            "temperature": EigerParameter(22.5, "float", "r"),
            "humidity": EigerParameter(32.1, "float", "r"),
        },
    }


async def _oscillate_temperature(
    parameter: EigerParameter,
    low: float = 20.0,
    high: float = 30.0,
    period: float = 10.0,
) -> None:
    """Slowly sweep a temperature parameter between two values, forever.

    Gives the front end something visibly changing to poll. Runs as a background
    task under the app's lifespan (started by a real server, e.g. uvicorn; not by
    the in-process ASGI transport used in tests, which keeps those deterministic).
    """
    mid = (low + high) / 2
    amplitude = (high - low) / 2
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        parameter.value = round(
            mid + amplitude * math.sin(2 * math.pi * elapsed / period), 1
        )
        await asyncio.sleep(0.1)


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
        return {
            "value": parameter.value,
            "value_type": parameter.value_type,
            "access_mode": parameter.access_mode,
        }

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
