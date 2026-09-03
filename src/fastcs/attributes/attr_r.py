from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Unpack, overload

from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.attribute_io_ref import AttributeIORefT
from fastcs.attributes.util import AttrValuePredicate, PredicateEvent
from fastcs.datatypes import (
    Array1DMeta,
    Array_T,
    BoolMeta,
    DType_T,
    Enum_T,
    EnumMeta,
    FloatMeta,
    IntMeta,
    StrMeta,
    Table,
    TableMeta,
)
from fastcs.logging import logger

AttrIOUpdateCallback = Callable[["AttrR[DType_T, Any]"], Coroutine[None, None, None]]
"""An AttributeIO callback that takes an AttrR and updates its value"""
AttrUpdateCallback = Callable[[], Coroutine[None, None, None]]
"""A callback to be called periodically to update an attribute"""
AttrOnUpdateCallback = Callable[[DType_T], Coroutine[None, None, None]]
"""A callback to be called when the value of the attribute is updated"""


class AttrR(Attribute[DType_T, AttributeIORefT]):
    """A read-only ``Attribute``"""

    # One overload per datatype, so that metadata a datatype has no use for is
    # a type error rather than a field silently ignored: ``AttrR(str,
    # precision=3)`` does not type check.
    #
    # Overload resolution takes the first datatype a call matches, and ``bool``
    # matches ``int`` while ``int`` matches ``float``. So ``AttrR(bool,
    # units=...)`` resolves to the ``int`` overload rather than failing here -
    # the constructor's runtime check is what rejects it. A call whose metadata
    # is valid always picks its own datatype's overload.
    @overload
    def __init__(
        self: AttrR[bool, AttributeIORefT],
        datatype: type[bool],
        io_ref: AttributeIORefT | None = None,
        initial_value: bool | None = None,
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[int, AttributeIORefT],
        datatype: type[int],
        io_ref: AttributeIORefT | None = None,
        initial_value: int | None = None,
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[float, AttributeIORefT],
        datatype: type[float],
        io_ref: AttributeIORefT | None = None,
        initial_value: float | None = None,
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[str, AttributeIORefT],
        datatype: type[str],
        io_ref: AttributeIORefT | None = None,
        initial_value: str | None = None,
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[Enum_T, AttributeIORefT],
        datatype: type[Enum_T],
        io_ref: AttributeIORefT | None = None,
        initial_value: Enum_T | None = None,
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[Table, AttributeIORefT],
        datatype: type[Table],
        io_ref: AttributeIORefT | None = None,
        initial_value: Table | None = None,
        **meta: Unpack[TableMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrR[Array_T, AttributeIORefT],
        datatype: type[Array_T],
        io_ref: AttributeIORefT | None = None,
        initial_value: Array_T | None = None,
        **meta: Unpack[Array1DMeta],
    ) -> None: ...

    def __init__(
        self,
        datatype: Any,
        io_ref: AttributeIORefT | None = None,
        initial_value: Any = None,
        **meta: Any,
    ) -> None:
        super().__init__(datatype, io_ref, **meta)
        self._value: DType_T = (
            self.default_value() if initial_value is None else initial_value
        )
        self._update_callback: AttrIOUpdateCallback[DType_T] | None = None
        """Callback to update the value of the attribute with an IO to the source"""
        self._on_update_callbacks: (
            list[tuple[AttrOnUpdateCallback[DType_T], bool]] | None
        ) = None
        """Callbacks to publish changes to the value of the attribute"""
        self._on_update_events: set[PredicateEvent[DType_T]] = set()
        """Events to set when the value satisifies some predicate"""

    def get(self) -> DType_T:
        """Get the cached value of the attribute."""
        return self._value

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "r"

    async def update(self, value: Any) -> None:
        """Update the value of the attibute

        This sets the cached value of the attribute presented in the API. It should
        generally only be called from an IO or a controller that is updating the value
        from some underlying source.

        Any update callbacks will be called with the new value and any update events
        with predicates satisfied by the new value will be set.

        To request a change to the setpoint of the attribute, use the ``put`` method,
        which will attempt to apply the change to the underlying source.

        Args:
            value: The new value of the attribute

        Raises:
            ValueError: If the value fails to be validated to DType_T

        """
        self.log_event("Attribute set", value=repr(value), attribute=self)

        _previous_value = self._value
        try:
            self._value = self.validate(value)
        except ValueError:
            logger.error("Failed to validate value", value=repr(value), attribute=self)
            raise

        self.log_event("Value validated", value=repr(self._value), attribute=self)

        self._on_update_events -= {
            e for e in self._on_update_events if e.set(self._value)
        }

        if self._on_update_callbacks is not None:
            callbacks_to_call: list[AttrOnUpdateCallback[DType_T]] = [
                cb
                for cb, always in self._on_update_callbacks
                if always or not self.equal(self._value, _previous_value)
            ]
            try:
                await asyncio.gather(*[cb(self._value) for cb in callbacks_to_call])
            except Exception as e:
                logger.opt(exception=e).error(
                    "On update callbacks failed",
                    attribute=self,
                    value=repr(self._value),
                )
                raise

    def add_on_update_callback(
        self, callback: AttrOnUpdateCallback[DType_T], always: bool = False
    ) -> None:
        """Add a callback to be called when the value of the attribute is updated

        The callback will be called with the updated value.

        """
        if self._on_update_callbacks is None:
            self._on_update_callbacks = []
        self._on_update_callbacks.append((callback, always))

    def set_update_callback(self, callback: AttrIOUpdateCallback[DType_T]):
        """Set the callback to update the value of the attribute from the source

        The callback will be converted to an async task and called periodically.

        """
        if self._update_callback is not None:
            raise RuntimeError("Attribute already has an IO update callback")

        self._update_callback = callback

    def bind_update_callback(self) -> AttrUpdateCallback:
        """Bind self into the registered IO update callback"""
        if self._update_callback is None:
            raise RuntimeError("Attribute has no update callback")
        else:
            update_callback = self._update_callback

        async def update_attribute():
            self.log_event("Update attribute", topic=self)
            await update_callback(self)

        return update_attribute

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
