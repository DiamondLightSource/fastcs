from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Unpack, overload

from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.attribute_io_ref import AttributeIORefT
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

AttrOnPutCallback = Callable[["AttrW[DType_T, Any]", DType_T], Awaitable[None]]
"""Callbacks to be called when the setpoint of an attribute is changed"""
AttrSyncSetpointCallback = Callable[[DType_T], Awaitable[None]]
"""Callbacks to be called when the setpoint of an attribute is changed"""


class AttrW(Attribute[DType_T, AttributeIORefT]):
    """A write-only ``Attribute``."""

    # One overload per datatype, so that metadata a datatype has no use for is
    # a type error rather than a field silently ignored: ``AttrW(str,
    # precision=3)`` does not type check.
    #
    # Overload resolution takes the first datatype a call matches, and ``bool``
    # matches ``int`` while ``int`` matches ``float``. So ``AttrW(bool,
    # units=...)`` resolves to the ``int`` overload rather than failing here -
    # the constructor's runtime check is what rejects it. A call whose metadata
    # is valid always picks its own datatype's overload.
    @overload
    def __init__(
        self: AttrW[bool, AttributeIORefT],
        datatype: type[bool],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[int, AttributeIORefT],
        datatype: type[int],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[float, AttributeIORefT],
        datatype: type[float],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[str, AttributeIORefT],
        datatype: type[str],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Enum_T, AttributeIORefT],
        datatype: type[Enum_T],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Table, AttributeIORefT],
        datatype: type[Table],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[TableMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Array_T, AttributeIORefT],
        datatype: type[Array_T],
        io_ref: AttributeIORefT | None = None,
        **meta: Unpack[Array1DMeta],
    ) -> None: ...

    def __init__(
        self,
        datatype: Any,
        io_ref: AttributeIORefT | None = None,
        **meta: Any,
    ) -> None:
        super().__init__(datatype, io_ref, **meta)
        self._on_put_callback: AttrOnPutCallback[DType_T] | None = None
        """Callback to action a change to the setpoint of the attribute"""
        self._sync_setpoint_callbacks: list[AttrSyncSetpointCallback[DType_T]] = []
        """Callbacks to publish changes to the setpoint of the attribute"""

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "w"

    async def put(self, setpoint: DType_T, sync_setpoint: bool = False) -> None:
        """Set the setpoint of the attribute

        This should be called by clients to the attribute such as transports to apply a
        change to the attribute. The ``_on_put_callback`` will be called with this new
        setpoint, which may or may not take effect depending on the validity of the new
        value. For example, if the attribute has an IO to some device, the value might
        be rejected.

        To directly change the value of the attribute, for example from an update loop
        that has read a new value from some underlying source, call `AttrR.update`.

        """
        setpoint = self.validate(setpoint)
        if self._on_put_callback is not None:
            try:
                await self._on_put_callback(self, setpoint)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Put failed", attribute=self, setpoint=setpoint
                )
                raise

        if sync_setpoint:
            try:
                await self._call_sync_setpoint_callbacks(setpoint)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Sync setpoint failed", attribute=self, setpoint=setpoint
                )

        self.log_event("Put complete", setpoint=setpoint, attribute=self)

    async def _call_sync_setpoint_callbacks(self, setpoint: DType_T) -> None:
        if self._sync_setpoint_callbacks:
            await asyncio.gather(
                *[cb(setpoint) for cb in self._sync_setpoint_callbacks]
            )

    def set_on_put_callback(self, callback: AttrOnPutCallback[DType_T]) -> None:
        """Set the callback to call when the setpoint is changed

        The callback will be called with the attribute and the new setpoint.

        """
        if self._on_put_callback is not None:
            raise RuntimeError("Attribute already has an on put callback")

        self._on_put_callback = callback

    def add_sync_setpoint_callback(
        self, callback: AttrSyncSetpointCallback[DType_T]
    ) -> None:
        """Add a callback to publish changes to the setpoint of the attribute

        The callback will be called with the new setpoint.

        """
        self._sync_setpoint_callbacks.append(callback)
