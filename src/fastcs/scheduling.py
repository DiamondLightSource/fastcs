"""The periodic and read-once work of a group of controllers.

Each `Supervisor` owns one of these for the controllers holding its connection, and
the `ControllerRunner` owns one for the soft controllers, which hold none. Grouping
the work by owner is what lets one connection's scans pause while it is down
without touching anybody else's.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from fastcs.attributes import AttrR, Severity, Update
from fastcs.exceptions import DisconnectedError
from fastcs.logging import logger
from fastcs.util import ONCE

if TYPE_CHECKING:
    from fastcs.controllers import BaseController

ScanCallback = Callable[[], Coroutine[None, None, None]]

ERROR_SUMMARY_PERIOD = 60.0
"""Seconds between reminders that a scan is still failing the same way."""


async def flag_invalid(attribute: AttrR[Any]) -> None:
    """Republish an attribute's last value marked as untrustworthy.

    The value and the time it was obtained are kept; only the severity changes, so
    a client sees a stale value flagged rather than a made-up one. The next good
    poll clears it.
    """
    if attribute.severity is Severity.INVALID:
        return

    await _publish(
        attribute,
        Update(
            attribute.readback,
            timestamp=attribute.timestamp,
            severity=Severity.INVALID,
        ),
    )


async def _publish(attribute: AttrR[Any], value: Any) -> None:
    """Update an attribute on the framework's behalf, never raising.

    ``update`` has already logged whatever went wrong - a failing transport
    callback, say - and a reconnect loop or scan task must not die of it.
    """
    with contextlib.suppress(Exception):
        await attribute.update(value)


class _ErrorReport:
    """Rate-limited logging for one scan that keeps failing.

    The first failure is logged in full, then only a periodic reminder with a count
    until the scan succeeds again - a device that rejects the same read every 0.1s
    should not bury everything else in the log.
    """

    def __init__(self, event: str, **context: Any) -> None:
        self._event = event
        self._context = context
        self._failures = 0
        self._last_logged = 0.0

    def failed(self, exc: Exception) -> None:
        now = time.monotonic()
        if self._failures == 0:
            logger.opt(exception=exc).error(self._event, **self._context)
            self._last_logged = now
        elif now - self._last_logged >= ERROR_SUMMARY_PERIOD:
            logger.error(
                f"{self._event} (still failing)",
                failures=self._failures + 1,
                error=repr(exc),
                **self._context,
            )
            self._last_logged = now
        self._failures += 1

    def succeeded(self) -> None:
        if self._failures:
            logger.info(
                f"{self._event} (recovered)", failures=self._failures, **self._context
            )
            self._failures = 0


def _guarded_poll(attribute: AttrR[Any]) -> ScanCallback:
    report = _ErrorReport("Poll failed", attribute=attribute)

    async def poll() -> None:
        try:
            await attribute.poll()
        except DisconnectedError:
            # Silent: the supervisor has already said the link is down.
            await flag_invalid(attribute)
        except Exception as exc:
            report.failed(exc)
        else:
            report.succeeded()

    return poll


def _guarded_scan(scan: Callable[[], Coroutine[Any, Any, Any]]) -> ScanCallback:
    report = _ErrorReport("Scan failed", fn=scan)

    async def run() -> None:
        try:
            await scan()
        except DisconnectedError:
            pass
        except Exception as exc:
            report.failed(exc)
        else:
            report.succeeded()

    return run


class ScanSchedule:
    """The read-once reads, periodic scans and ``connected`` flags of some controllers.

    Periodic work is grouped by period, one task per period, and every callback is
    guarded: a `DisconnectedError` flags the attribute that hit it and says nothing
    more, and anything else is logged, rate limited, and retried next period.
    """

    def __init__(self) -> None:
        self._initial: list[ScanCallback] = []
        self._periodic: dict[float, list[ScanCallback]] = defaultdict(list)
        self._polled: list[AttrR[Any]] = []
        self._connected: list[AttrR[bool]] = []

    def add_controller(self, controller: BaseController) -> None:
        """Take on the scans and polled attributes of one controller."""
        for method in controller.scan_methods.values():
            if method.period is ONCE:
                self._initial.append(method.__call__)
            else:
                self._periodic[method.period].append(_guarded_scan(method.__call__))

        for attribute in controller.attributes.values():
            if not (isinstance(attribute, AttrR) and attribute.has_getter()):
                continue

            period = attribute.poll_period
            if period is ONCE:
                self._initial.append(attribute.poll)
            elif period is not None:
                self._periodic[period].append(_guarded_poll(attribute))
                self._polled.append(attribute)

    def add_connected_attribute(self, attribute: AttrR[bool]) -> None:
        """An attribute to keep in step with whether this work can run."""
        self._connected.append(attribute)

    @property
    def has_polling(self) -> bool:
        """Whether anything here runs periodically, and so would notice a failure."""
        return bool(self._periodic)

    @property
    def periods(self) -> list[float]:
        """The distinct periods of the periodic work, one task each."""
        return list(self._periodic)

    async def read_once(self) -> None:
        """Run every read-once read and scan. Raises the first failure."""
        for callback in self._initial:
            await callback()

    async def reread_once(self) -> None:
        """Run every read-once read and scan again, logging rather than raising."""
        for callback in self._initial:
            await _guarded_scan(callback)()

    async def set_connected(self, connected: bool) -> None:
        for attribute in self._connected:
            await _publish(attribute, connected)

    async def flag_polled_invalid(self) -> None:
        """Mark every periodically read value as stale, since none can be read."""
        for attribute in self._polled:
            await flag_invalid(attribute)

    def start(
        self, loop: asyncio.AbstractEventLoop, gate: asyncio.Event | None
    ) -> set[asyncio.Task]:
        """Start one task per period. With a ``gate``, each waits while it is clear."""
        return {
            loop.create_task(_run_periodically(period, callbacks, gate))
            for period, callbacks in self._periodic.items()
        }


async def _run_periodically(
    period: float, callbacks: list[ScanCallback], gate: asyncio.Event | None
) -> None:
    while True:
        if gate is not None:
            await gate.wait()

        # Guarded, so this never raises; and sleeping alongside rather than after
        # keeps the period steady however long the callbacks take.
        await asyncio.gather(asyncio.sleep(period), *(cb() for cb in callbacks))
