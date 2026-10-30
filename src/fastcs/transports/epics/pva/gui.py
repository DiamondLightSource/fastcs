import numpy as np
from pvi.device import (
    CheckBox,
    ImageColorMap,
    ImageRead,
    ReadWidgetUnion,
    TableRead,
    TableWrite,
    WriteWidgetUnion,
)

from fastcs.attributes import Attribute, AttrR, AttrW
from fastcs.datatypes import (
    DEFAULT_ARRAY_SHAPE,
    numpy_to_python_type,
)
from fastcs.transports.epics.gui import EpicsGUI


class PvaEpicsGUI(EpicsGUI):
    """For creating gui in the PVA EPICS transport."""

    command_value = "true"

    def _get_pv(self, attr_path: list[str], name: str):
        return f"pva://{super()._get_pv(attr_path, name)}"

    def _get_read_widget(self, attribute: Attribute) -> ReadWidgetUnion | None:
        structured_dtype = attribute.meta.get("structured_dtype")
        if structured_dtype is not None:
            column_types = [
                numpy_to_python_type(column_dtype)
                for _, column_dtype in structured_dtype
            ]

            base_get_read_widget = super()._get_read_widget
            widgets = [
                base_get_read_widget(AttrR(column_type)) for column_type in column_types
            ]

            return TableRead(widgets=widgets)  # type: ignore

        if issubclass(attribute.dtype, np.ndarray):
            shape = attribute.meta.get("shape", DEFAULT_ARRAY_SHAPE)
            if len(shape) == 2:
                height, width = shape
                return ImageRead(
                    height=height, width=width, color_map=ImageColorMap.GRAY
                )

        return super()._get_read_widget(attribute)

    def _get_write_widget(self, attribute: Attribute) -> WriteWidgetUnion | None:
        structured_dtype = attribute.meta.get("structured_dtype")
        if structured_dtype is not None:
            widgets = []
            for _, column_dtype in structured_dtype:
                column_type = numpy_to_python_type(column_dtype)
                if column_type is bool:
                    # Replace with compact version for Table row
                    widget = CheckBox()
                else:
                    widget = super()._get_write_widget(AttrW(column_type))
                widgets.append(widget)
            return TableWrite(widgets=widgets)

        return super()._get_write_widget(attribute)
