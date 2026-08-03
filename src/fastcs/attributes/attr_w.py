from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastcs.attributes._infer_datatype import infer_datatype_from_setter
from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.datatypes import DataType, DType_T
from fastcs.logging import logger

Setter = Callable[[DType_T], Awaitable[None | DType_T | Update[DType_T]]]
"""A callable that applies a new setpoint to an attribute's source"""
AttrSetpointCallback = Callable[[DType_T], Coroutine[None, None, None]]
"""A callback to be called when the setpoint of the attribute updates"""


class AttrW(Attribute[DType_T]):
    """A write-only ``Attribute``."""

    def __init__(
        self,
        datatype: DataType[DType_T] | None = None,
        setter: Setter[DType_T] | None = None,
        **kwargs: Any,
    ) -> None:
        if datatype is None and setter is not None:
            datatype = infer_datatype_from_setter(setter)

        super().__init__(datatype, **kwargs)

        self._setter = setter
        self._setpoint: DType_T = self._datatype.initial_value
        self._setpoint_known = False
        """Whether the setpoint reflects a real value rather than the datatype default

        Until something establishes it - a write, or (for an ``AttrRW``) the first
        readback - the setpoint is just the datatype's default and means nothing.
        """
        self._setpoint_callbacks: list[AttrSetpointCallback[DType_T]] = []
        """Callbacks to publish changes to the setpoint of the attribute"""

    @property
    def setpoint(self) -> DType_T:
        """The last-requested value of the attribute."""
        return self._setpoint

    def has_setter(self) -> bool:
        return self._setter is not None

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "w"

    def add_setpoint_callback(self, callback: AttrSetpointCallback[DType_T]) -> None:
        """Add a callback to be called when the setpoint of the attribute updates

        The callback will be called with the updated setpoint. Transports should
        use this to publish the attribute's setpoint rather than tracking their own,
        so that every transport agrees on it however the change was made.

        """
        self._setpoint_callbacks.append(callback)

    async def update_setpoint(self, value: DType_T) -> None:
        """Cache a new setpoint and publish it to the setpoint callbacks.

        This does no IO - it is the setpoint-side counterpart of ``AttrR.update``.

        """
        self._setpoint = self._datatype.validate(value)
        self._setpoint_known = True

        if self._setpoint_callbacks:
            try:
                await asyncio.gather(
                    *[cb(self._setpoint) for cb in self._setpoint_callbacks]
                )
            except Exception as e:
                logger.opt(exception=e).error(
                    "Setpoint callbacks failed",
                    attribute=self,
                    setpoint=repr(self._setpoint),
                )
                raise

    def _setter_result_setpoint(
        self, result: DType_T | Update[DType_T]
    ) -> DType_T | None:
        """The setpoint a setter's return value asks for, if any."""
        if isinstance(result, Update):
            # A bare readback with no setpoint leaves the cached setpoint alone.
            return result.setpoint
        # A bare value is the device's accepted/clamped value - both what it will
        # report and what it understood us to ask for.
        return result

    async def set(self, value: DType_T) -> None:
        """Request a new value for the attribute

        This should be called by clients to the attribute such as transports to apply
        a change to the attribute. ``value`` is cached as the setpoint, then the
        setter (if any) is called to apply it to the underlying source - the value
        might be rejected or clamped, depending on the validity of the new value. If
        the setter returns a value, that is treated as the source's accepted/clamped
        value and becomes the new cached setpoint.

        To directly change the readback of an attribute, for example from an update
        loop that has read a new value from some underlying source, call
        ``AttrR.update``.

        """
        await self.update_setpoint(value)

        if self._setter is not None:
            try:
                result = await self._setter(self._setpoint)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Set failed", attribute=self, setpoint=self._setpoint
                )
            else:
                if result is not None:
                    accepted = self._setter_result_setpoint(result)
                    if accepted is not None:
                        await self.update_setpoint(accepted)

        self.log_event("Set complete", setpoint=self._setpoint, attribute=self)
