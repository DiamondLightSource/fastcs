from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastcs.attributes._infer_datatype import infer_datatype_from_setter
from fastcs.attributes.attribute import Attribute, AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.datatypes import DataType, DType_T
from fastcs.logging import logger

Setter = Callable[[DType_T], Awaitable["None | DType_T | Update[DType_T]"]]
"""A callable that applies a new setpoint to an attribute's source"""


class AttrW(Attribute[DType_T]):
    """A write-only ``Attribute``."""

    def __init__(
        self,
        datatype: DataType[DType_T] | None = None,
        setter: Setter[DType_T] | None = None,
        group: str | None = None,
        description: str | None = None,
    ) -> None:
        if datatype is None:
            datatype = setter and infer_datatype_from_setter(setter)
            if datatype is None:
                raise ValueError(
                    "datatype must be given explicitly, or be inferable from the "
                    "setter's value parameter annotation"
                )

        Attribute.__init__(self, datatype, group, description=description)
        self._setter = setter
        self._setpoint: DType_T = datatype.initial_value

    @property
    def setpoint(self) -> DType_T:
        """The last-requested value of the attribute."""
        return self._setpoint

    def has_setter(self) -> bool:
        return self._setter is not None

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "w"

    async def set(self, value: DType_T) -> None:
        """Request a new value for the attribute

        This should be called by clients to the attribute such as transports to apply
        a change to the attribute. ``value`` is cached as the setpoint, then the
        setter (if any) is called to apply it to the underlying source - the value
        might be rejected or clamped, depending on the validity of the new value. If
        the setter returns a non-``None`` value, that is treated as the source's
        accepted/clamped value and becomes the new cached setpoint.

        To directly change the readback of an attribute, for example from an update
        loop that has read a new value from some underlying source, call
        ``AttrR.update``.

        """
        value = self._datatype.validate(value)
        self._setpoint = value

        if self._setter is not None:
            try:
                result = await self._setter(value)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Set failed", attribute=self, setpoint=value
                )
            else:
                if result is not None:
                    self._setpoint = (
                        result.value if isinstance(result, Update) else result
                    )

        self.log_event("Set complete", setpoint=self._setpoint, attribute=self)
