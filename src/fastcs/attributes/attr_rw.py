from __future__ import annotations

from fastcs.attributes._infer_datatype import (
    infer_datatype_from_getter,
    infer_datatype_from_setter,
)
from fastcs.attributes.attr_r import AttrR, Getter
from fastcs.attributes.attr_w import AttrW, Setter
from fastcs.attributes.attribute import AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.datatypes import DataType, DType_T
from fastcs.logging import logger

_UNSET = object()


class AttrRW(AttrR[DType_T], AttrW[DType_T]):
    """A read-write ``Attribute``."""

    def __init__(
        self,
        datatype: DataType[DType_T] | None = None,
        getter: Getter[DType_T] | None = None,
        setter: Setter[DType_T] | None = None,
        poll_period: float | None = _UNSET,  # type: ignore[assignment]
        group: str | None = None,
        initial_value: DType_T | None = None,
        description: str | None = None,
    ):
        if datatype is None:
            datatype = (getter and infer_datatype_from_getter(getter)) or (
                setter and infer_datatype_from_setter(setter)
            )
            if datatype is None:
                raise ValueError(
                    "datatype must be given explicitly, or be inferable from the "
                    "getter/setter annotations"
                )

        AttrR.__init__(
            self, datatype, getter, poll_period, group, initial_value, description
        )
        AttrW.__init__(self, datatype, setter, group, description)
        # Soft/RW attrs start with setpoint == readback, rather than the datatype's
        # generic initial_value.
        self._setpoint = self._value

    @property
    def access_mode(self) -> AttributeAccessMode:
        return "rw"

    async def set(self, value: DType_T) -> None:
        """Request a new value for the attribute.

        With no setter, this is a soft attribute: the requested value is pushed
        straight to the readback. With a setter, a non-``None`` return value is
        additionally applied to the readback - the sanctioned replacement for the
        old private setpoint-echo mechanism.

        """
        value = self._datatype.validate(value)
        self._setpoint = value

        if self._setter is None:
            await self.update(value)
        else:
            try:
                result = await self._setter(value)
            except Exception as e:
                logger.opt(exception=e).error(
                    "Set failed", attribute=self, setpoint=value
                )
            else:
                if result is not None:
                    accepted = result.value if isinstance(result, Update) else result
                    self._setpoint = accepted
                    await self.update(accepted)

        self.log_event("Set complete", setpoint=self._setpoint, attribute=self)
