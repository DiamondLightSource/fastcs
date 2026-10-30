from __future__ import annotations

from typing import Any, Unpack, overload

from fastcs.attributes.attr_r import AttrR
from fastcs.attributes.attr_w import AttrW
from fastcs.attributes.attribute import AttributeAccessMode
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


class AttrRW(AttrR[DType_T, AttributeIORefT], AttrW[DType_T, AttributeIORefT]):
    """A read-write ``Attribute``."""

    # One overload per datatype, so that metadata a datatype has no use for is
    # a type error rather than a field silently ignored: ``AttrRW(str,
    # precision=3)`` does not type check.
    #
    # Overload resolution takes the first datatype a call matches, and ``bool``
    # matches ``int`` while ``int`` matches ``float``. So ``AttrRW(bool,
    # units=...)`` resolves to the ``int`` overload rather than failing here -
    # the constructor's runtime check is what rejects it. A call whose metadata
    # is valid always picks its own datatype's overload.
    @overload
    def __init__(
        self: AttrRW[bool, AttributeIORefT],
        datatype: type[bool],
        io_ref: AttributeIORefT | None = None,
        initial_value: bool | None = None,
        **meta: Unpack[BoolMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[int, AttributeIORefT],
        datatype: type[int],
        io_ref: AttributeIORefT | None = None,
        initial_value: int | None = None,
        **meta: Unpack[IntMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[float, AttributeIORefT],
        datatype: type[float],
        io_ref: AttributeIORefT | None = None,
        initial_value: float | None = None,
        **meta: Unpack[FloatMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[str, AttributeIORefT],
        datatype: type[str],
        io_ref: AttributeIORefT | None = None,
        initial_value: str | None = None,
        **meta: Unpack[StrMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Enum_T, AttributeIORefT],
        datatype: type[Enum_T],
        io_ref: AttributeIORefT | None = None,
        initial_value: Enum_T | None = None,
        **meta: Unpack[EnumMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Table, AttributeIORefT],
        datatype: type[Table],
        io_ref: AttributeIORefT | None = None,
        initial_value: Table | None = None,
        **meta: Unpack[TableMeta],
    ) -> None: ...

    @overload
    def __init__(
        self: AttrRW[Array_T, AttributeIORefT],
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
    ):
        super().__init__(datatype, io_ref, initial_value, **meta)

        self._setpoint_initialised = False

        if io_ref is None:
            self.set_on_put_callback(self._internal_update)

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "rw"

    async def _internal_update(
        self, attr: AttrW[DType_T, AttributeIORefT], value: DType_T
    ):
        """Update value directly when Attribute has no IO"""
        assert attr is self
        await self.update(value)

    async def update(self, value: DType_T):
        await super().update(value)

        if not self._setpoint_initialised:
            await self._call_sync_setpoint_callbacks(self._value)
            self._setpoint_initialised = True
