from __future__ import annotations

from typing import Any, Unpack, overload

from fastcs.attributes.attr_r import AttrR, Getter, Schedule
from fastcs.attributes.attr_w import AttrW, Setter
from fastcs.attributes.attribute import AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.datatypes import (
    Array1DMeta,
    Array_T,
    BoolMeta,
    DType_T,
    Enum_T,
    EnumMeta,
    FloatMeta,
    Inferred_T,
    IntMeta,
    Meta,
    StrMeta,
    Table,
    TableMeta,
)
from fastcs.logging import logger


class AttrRW(AttrR[DType_T], AttrW[DType_T]):
    """A read-write ``Attribute``."""

    # One overload per datatype, so that metadata a datatype has no use for is
    # a type error rather than a field silently ignored: ``AttrRW(str,
    # precision=3)`` does not type check. The last overload is the
    # inferred-datatype case, where the datatype is only known from the
    # getter/setter annotation, so the metadata is checked at runtime.
    #
    # Overload resolution takes the first datatype a call matches, and ``bool``
    # matches ``int`` while ``int`` matches ``float``. So ``AttrRW(bool,
    # units=...)`` resolves to the ``int`` overload rather than failing here -
    # the constructor's runtime check is what rejects it. A call whose metadata
    # is valid always picks its own datatype's overload.
    @overload
    def __init__(
        self: AttrRW[bool],
        datatype: type[bool],
        getter: Getter[bool] | Schedule[bool] | None = None,
        setter: Setter[bool] | None = None,
        initial_value: bool | None = None,
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[int],
        datatype: type[int],
        getter: Getter[int] | Schedule[int] | None = None,
        setter: Setter[int] | None = None,
        initial_value: int | None = None,
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[float],
        datatype: type[float],
        getter: Getter[float] | Schedule[float] | None = None,
        setter: Setter[float] | None = None,
        initial_value: float | None = None,
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[str],
        datatype: type[str],
        getter: Getter[str] | Schedule[str] | None = None,
        setter: Setter[str] | None = None,
        initial_value: str | None = None,
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Enum_T],
        datatype: type[Enum_T],
        getter: Getter[Enum_T] | Schedule[Enum_T] | None = None,
        setter: Setter[Enum_T] | None = None,
        initial_value: Enum_T | None = None,
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Table],
        datatype: type[Table],
        getter: Getter[Table] | Schedule[Table] | None = None,
        setter: Setter[Table] | None = None,
        initial_value: Table | None = None,
        **meta: Unpack[TableMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Array_T],
        datatype: type[Array_T],
        getter: Getter[Array_T] | Schedule[Array_T] | None = None,
        setter: Setter[Array_T] | None = None,
        initial_value: Array_T | None = None,
        **meta: Unpack[Array1DMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Inferred_T],
        datatype: None = None,
        getter: Getter[Inferred_T] | Schedule[Inferred_T] | None = None,
        setter: Setter[Inferred_T] | None = None,
        initial_value: Inferred_T | None = None,
        **meta: Unpack[Meta],
    ) -> None: ...

    def __init__(
        self,
        datatype: Any = None,
        getter: Any = None,
        setter: Any = None,
        initial_value: Any = None,
        **meta: Any,
    ):
        # There is no datatype handling to do here. ``AttrR`` infers it from the
        # getter and ``AttrW`` from the setter; the MRO runs both in turn, so
        # whichever can resolve it does, and ``Attribute`` makes the final check.
        # ``setter`` travels through ``AttrR`` to ``AttrW`` the same way, which
        # the public overloads - describing what a caller may pass - do not show.
        super().__init__(
            datatype,
            getter=getter,
            setter=setter,  # pyright: ignore[reportCallIssue]
            initial_value=initial_value,
            **meta,
        )

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "rw"

    async def update(self, value: DType_T | Update[DType_T]) -> None:
        """Update the readback of the attribute, and its setpoint if appropriate.

        An ``Update`` carrying a ``setpoint`` publishes that too - the mechanism for
        a device that reports its own setpoint. Otherwise, the first readback to
        arrive establishes the setpoint, so that a setpoint display shows the
        device's value rather than the datatype's default until first written.

        """
        await super().update(value)

        if isinstance(value, Update) and value.setpoint is not None:
            await self.update_setpoint(value.setpoint)
        elif not self._setpoint_known:
            await self.update_setpoint(self._value)

    async def set(self, value: DType_T) -> None:
        """Request a new value for the attribute.

        With no setter, this is a soft attribute: the requested value is pushed
        straight to the readback. With a setter, a returned value is treated as the
        device's accepted/clamped value and is applied to the readback as well as
        the setpoint.

        """
        await self.update_setpoint(value)

        if self._setter is None:
            await self.update(self._setpoint)
        else:
            try:
                result = await self._setter(self._setpoint)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Set failed", attribute=self, setpoint=self._setpoint
                )
            else:
                if isinstance(result, Update):
                    await self.update(result)
                elif result is not None:
                    # A bare value is the device's accepted/clamped value - both the
                    # new readback and what it understood us to ask for.
                    await self.update_setpoint(result)
                    await self.update(result)

        self.log_event("Set complete", setpoint=self._setpoint, attribute=self)
