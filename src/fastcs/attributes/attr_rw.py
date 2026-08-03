from __future__ import annotations

from typing import Any

from fastcs.attributes.attr_r import AttrR, Getter, Schedule
from fastcs.attributes.attr_w import AttrW, Setter
from fastcs.attributes.attribute import AttributeAccessMode
from fastcs.attributes.update import Update
from fastcs.datatypes import DataType, DType_T
from fastcs.logging import logger


class AttrRW(AttrR[DType_T], AttrW[DType_T]):
    """A read-write ``Attribute``."""

    def __init__(
        self,
        datatype: DataType[DType_T] | None = None,
        getter: Getter[DType_T] | Schedule[DType_T] | None = None,
        setter: Setter[DType_T] | None = None,
        initial_value: DType_T | None = None,
        **kwargs: Any,
    ):
        # There is no datatype handling to do here. ``AttrR`` infers it from the
        # getter and ``AttrW`` from the setter; the MRO runs both in turn, so
        # whichever can resolve it does, and ``Attribute`` makes the final check.
        super().__init__(
            datatype,
            getter=getter,
            setter=setter,
            initial_value=initial_value,
            **kwargs,
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
        straight to the readback. With a setter, a returned value is additionally
        applied to the readback - the sanctioned replacement for the old private
        setpoint-echo mechanism.

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
