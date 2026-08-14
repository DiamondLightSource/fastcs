from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import KW_ONLY, dataclass, replace
from typing import Any, Generic

from fastcs.attributes._infer_datatype import infer_datatype_from_getter
from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.attributes.util import AttrValuePredicate, PredicateEvent
from fastcs.datatypes import DataType, DType_T
from fastcs.logging import logger
from fastcs.util import ONCE

Getter = Callable[[], Awaitable[DType_T | Update[DType_T]]]
"""A callable that fetches a fresh value for an attribute from its source"""
AttrReadbackCallback = Callable[[DType_T], Coroutine[None, None, None]]
"""A callback to be called when the readback of the attribute updates"""


@dataclass
class Polled(Generic[DType_T]):
    """A getter to be read repeatedly, every ``period`` seconds::

        AttrR(getter=Polled(protocol.get_temperature, period=0.1))

    Use this for values the device changes on its own, such as readings and status.
    A getter passed without a schedule is read once, when the controller connects.
    """

    getter: Getter[DType_T] | None = None
    _: KW_ONLY
    period: float

    def __call__(self, getter: Getter[DType_T]) -> Polled[DType_T]:
        """Bind a getter, so a schedule can also be applied as a decorator."""
        return replace(self, getter=getter)


@dataclass
class NotPolled(Generic[DType_T]):
    """A getter that is never read on a schedule::

        AttrR(getter=NotPolled(protocol.get_label))

    The value is only set explicitly - from a ``@scan`` or a subscription calling
    ``attr.update()`` - or read on demand with ``await attr.poll()``. This is not the
    same as an attribute with no getter at all, which has nothing to read.
    """

    getter: Getter[DType_T] | None = None

    def __call__(self, getter: Getter[DType_T]) -> NotPolled[DType_T]:
        """Bind a getter, so a schedule can also be applied as a decorator."""
        return replace(self, getter=getter)


Schedule = Polled[DType_T] | NotPolled[DType_T]
"""A getter with a reading schedule attached"""


class AttrR(Attribute[DType_T]):
    """A read-only ``Attribute``"""

    def __init__(
        self,
        datatype: DataType[DType_T] | None = None,
        getter: Getter[DType_T] | Schedule[DType_T] | None = None,
        initial_value: DType_T | None = None,
        **kwargs: Any,
    ) -> None:
        match getter:
            case Polled() | NotPolled():
                if getter.getter is None:
                    raise ValueError(
                        f"{type(getter).__name__} was given no getter to schedule"
                    )
                resolved_getter = getter.getter
                poll_period = getter.period if isinstance(getter, Polled) else None
            case None:
                resolved_getter, poll_period = None, None
            case _:
                # A getter with no schedule is read once, when the controller
                # connects - the safe default, and what a bare ``@attr`` means.
                resolved_getter, poll_period = getter, ONCE

        if datatype is None and resolved_getter is not None:
            datatype = infer_datatype_from_getter(resolved_getter)

        # Pass the datatype on rather than validating it here: in an ``AttrRW`` the
        # setter may still supply it, and ``Attribute`` makes the final check.
        super().__init__(datatype, **kwargs)

        self._value: DType_T = (
            self._datatype.initial_value if initial_value is None else initial_value
        )
        self._getter = resolved_getter
        self._poll_period: float | None = poll_period
        """Period in seconds between calls to poll(), or ONCE, or None (on-demand)"""
        self._readback_callbacks: (
            list[tuple[AttrReadbackCallback[DType_T], bool]] | None
        ) = None
        """Callbacks to publish changes to the readback of the attribute"""
        self._on_update_events: set[PredicateEvent[DType_T]] = set()
        """Events to set when the value satisifies some predicate"""

    @property
    def readback(self) -> DType_T:
        """The last known value of the attribute."""
        return self._value

    def has_getter(self) -> bool:
        return self._getter is not None

    @property
    def poll_period(self) -> float | None:
        return self._poll_period

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "r"

    async def update(self, value: DType_T | Update[DType_T]) -> None:
        """Update the value of the attribute

        This sets the cached value of the attribute presented in the API. It should
        generally only be called from a getter or a controller that is updating the
        value from some underlying source.

        Any update callbacks will be called with the new value and any update events
        with predicates satisfied by the new value will be set.

        To request a change to the setpoint of the attribute, use the ``set`` method,
        which will attempt to apply the change to the underlying source.

        Args:
            value: The new value of the attribute, or an ``Update`` wrapping it

        Raises:
            ValueError: If the value fails to be validated to DType_T

        """
        if isinstance(value, Update):
            value = value.readback

        self.log_event("Attribute set", value=repr(value), attribute=self)

        _previous_value = self._value
        try:
            self._value = self._datatype.validate(value)
        except ValueError:
            logger.error("Failed to validate value", value=repr(value), attribute=self)
            raise

        self.log_event("Value validated", value=repr(self._value), attribute=self)

        self._on_update_events -= {
            e for e in self._on_update_events if e.set(self._value)
        }

        if self._readback_callbacks is not None:
            callbacks_to_call: list[AttrReadbackCallback[DType_T]] = [
                cb
                for cb, always in self._readback_callbacks
                if always or not self.datatype.equal(self._value, _previous_value)
            ]
            try:
                await asyncio.gather(*[cb(self._value) for cb in callbacks_to_call])
            except Exception as e:
                logger.opt(exception=e).error(
                    "Readback callbacks failed",
                    attribute=self,
                    value=repr(self._value),
                )
                raise

    async def poll(self) -> DType_T:
        """Fetch a fresh value from the getter, cache it, and return it."""
        if self._getter is None:
            raise RuntimeError(f"{self} has no getter")

        self.log_event("Poll attribute", topic=self)
        result = await self._getter()
        await self.update(result)
        return self._value

    def add_readback_callback(
        self, callback: AttrReadbackCallback[DType_T], always: bool = False
    ) -> None:
        """Add a callback to be called when the readback of the attribute updates

        The callback will be called with the updated readback value. Transports
        should use this to publish the attribute's readback, and
        ``AttrW.add_setpoint_callback`` to publish its setpoint.

        Args:
            callback: The callback to call with the updated readback value
            always: Whether to call the callback on every ``update``, rather than
                only when the new value differs from the cached one. Defaults to
                ``False``, so an update that does not change the value is not
                published. Pass ``True`` for a callback that must see every update
                - one that timestamps it, or counts it, rather than displaying it.

        """
        if self._readback_callbacks is None:
            self._readback_callbacks = []
        self._readback_callbacks.append((callback, always))

    async def wait_for_predicate(
        self, predicate: AttrValuePredicate[DType_T], *, timeout: float
    ):
        """Wait for the predicate to be satisfied when called with the current value

        Args:
            predicate: The predicate to test - a callable that takes the attribute
                value and returns True if the event should be set
            timeout: The timeout in seconds

        """
        if predicate(self._value):
            self.log_event(
                "Predicate already satisfied", predicate=predicate, attribute=self
            )
            return

        self._on_update_events.add(update_event := PredicateEvent(predicate))

        self.log_event("Waiting for predicate", predicate=predicate, attribute=self)
        try:
            await asyncio.wait_for(update_event.wait(), timeout)
        except TimeoutError:
            self._on_update_events.remove(update_event)
            raise TimeoutError(
                f"Timeout waiting {timeout}s for {self.full_name} predicate {predicate}"
                f" - current value: {self._value}"
            ) from None

        self.log_event("Predicate satisfied", predicate=predicate, attribute=self)

    async def wait_for_value(self, target_value: DType_T, *, timeout: float):
        """Wait for self._value to equal the target value

        Args:
            target_value: The target value to wait for
            timeout: The timeout in seconds

        Raises:
            TimeoutError: If the attribute does not reach the target value within the
                timeout

        """
        if self._value == target_value:
            self.log_event(
                "Current value already equals target value",
                target_value=target_value,
                attribute=self,
            )
            return

        def predicate(v: DType_T) -> bool:
            return v == target_value

        try:
            await self.wait_for_predicate(predicate, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(
                f"Timeout waiting {timeout}s for {self.full_name} value {target_value}"
                f" - current value: {self._value}"
            ) from None

        self.log_event(
            "Value equals target value", target_value=target_value, attribute=self
        )
