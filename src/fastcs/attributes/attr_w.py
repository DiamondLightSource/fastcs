from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Unpack, overload

from fastcs.attributes._infer_datatype import infer_datatype_from_setter
from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.update import Update
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
    check_control_limits,
)
from fastcs.logging import logger

Setter = Callable[[DType_T], Awaitable[None | DType_T | Update[DType_T]]]
"""A callable that applies a new setpoint to an attribute's source"""
AttrSetpointCallback = Callable[[DType_T], Coroutine[None, None, None]]
"""A callback to be called when the setpoint of the attribute updates"""


class AttrW(Attribute[DType_T]):
    """A write-only ``Attribute``."""

    # One overload per datatype, so that metadata a datatype has no use for is
    # a type error rather than a field silently ignored: ``AttrW(str,
    # precision=3)`` does not type check. The datatype may be left out when the
    # setter's value annotation gives it.
    #
    # Overload resolution takes the first datatype a call matches, and ``bool``
    # matches ``int`` while ``int`` matches ``float``. So ``AttrW(bool,
    # units=...)`` resolves to the ``int`` overload rather than failing here -
    # the constructor's runtime check is what rejects it. A call whose metadata
    # is valid always picks its own datatype's overload.
    # A setter given without a datatype or getter binds the datatype from its
    # value parameter. A setter accepting ``float`` also fits ``Setter[int]`` and
    # ``Setter[bool]``, so these overloads come first and run from the widest type
    # to the narrowest, so that the setter's own type is the first to match -
    # including arrays before ``Table``, which subclasses ``np.ndarray``.
    @overload
    def __init__(
        self: AttrW[float],
        datatype: None = None,
        *,
        setter: Setter[float],
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[int],
        datatype: None = None,
        *,
        setter: Setter[int],
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[bool],
        datatype: None = None,
        *,
        setter: Setter[bool],
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[str],
        datatype: None = None,
        *,
        setter: Setter[str],
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Enum_T],
        datatype: None = None,
        *,
        setter: Setter[Enum_T],
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Array_T],
        datatype: None = None,
        *,
        setter: Setter[Array_T],
        **meta: Unpack[Array1DMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Table],
        datatype: None = None,
        *,
        setter: Setter[Table],
        **meta: Unpack[TableMeta],
    ) -> None: ...

    # Otherwise the datatype comes from ``datatype``, or a getter's annotation.
    @overload
    def __init__(
        self: AttrW[bool],
        datatype: type[bool] | None = None,
        setter: Setter[bool] | None = None,
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[int],
        datatype: type[int] | None = None,
        setter: Setter[int] | None = None,
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[float],
        datatype: type[float] | None = None,
        setter: Setter[float] | None = None,
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[str],
        datatype: type[str] | None = None,
        setter: Setter[str] | None = None,
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Enum_T],
        datatype: type[Enum_T] | None = None,
        setter: Setter[Enum_T] | None = None,
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Table],
        datatype: type[Table] | None = None,
        setter: Setter[Table] | None = None,
        **meta: Unpack[TableMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrW[Array_T],
        datatype: type[Array_T] | None = None,
        setter: Setter[Array_T] | None = None,
        **meta: Unpack[Array1DMeta],
    ) -> None: ...

    def __init__(
        self,
        datatype: Any = None,
        setter: Any = None,
        **meta: Any,
    ) -> None:
        if datatype is None and setter is not None:
            datatype = infer_datatype_from_setter(setter)

        super().__init__(datatype, **meta)

        self._setter = setter
        self._setpoint: DType_T = self.default_value()
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
        self._setpoint = self.validate(value)
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
            # No setpoint leaves the cached setpoint alone - which ``set`` has
            # already made the requested value.
            return result.setpoint
        # A bare value is the device's accepted/clamped value - both what it will
        # report and what it understood us to ask for.
        return result

    async def set(self, value: DType_T) -> None:
        """Request a new value for the attribute

        This should be called by clients to the attribute such as transports to apply
        a change to the attribute. ``value`` is cached as the setpoint and published
        to the setpoint callbacks, so it is visible immediately, then passed to the
        setter (if any) to apply it to the underlying source. If the setter returns a
        value, that is treated as the source's accepted/clamped value and replaces
        the setpoint. See ADR 20.

        To directly change the readback of an attribute, for example from an update
        loop that has read a new value from some underlying source, call
        ``AttrR.update``.

        Args:
            value: The requested value

        Raises:
            ValueError: If the value cannot be coerced, or is outside the control
                limits - the setpoint is not changed
            Exception: Whatever the setter raises. The setpoint keeps the requested
                value, as an EPICS record does after a failed write; the exception
                propagates so that the transport can raise an alarm

        """
        setpoint = self.validate(value)
        check_control_limits(self._meta, setpoint)
        await self.update_setpoint(setpoint)

        if self._setter is not None:
            try:
                result = await self._setter(setpoint)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Set failed", attribute=self, setpoint=setpoint
                )
                raise

            if result is not None:
                accepted = self._setter_result_setpoint(result)
                if accepted is not None:
                    await self.update_setpoint(accepted)

        self.log_event("Set complete", setpoint=self._setpoint, attribute=self)
