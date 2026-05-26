from pvi.device import (
    LED,
    ArrayTrace,
    ButtonPanel,
    ComboBox,
    ComponentUnion,
    Device,
    Grid,
    Group,
    ReadWidgetUnion,
    SignalR,
    SignalRW,
    SignalW,
    SignalX,
    SubScreen,
    TextFormat,
    TextRead,
    TextWrite,
    ToggleButton,
    Tree,
    WriteWidgetUnion,
)
from pydantic import ValidationError

from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.controllers import ControllerAPI
from fastcs.datatypes import (
    Bool,
    Enum,
    Float,
    Int,
    String,
    Waveform,
)
from fastcs.logging import logger
from fastcs.methods import Command
from fastcs.transports.epics.util import pv_prefix_from_path
from fastcs.util import snake_to_pascal


class EpicsGUI:
    """For creating gui in the EPICS transports."""

    command_value = "1"

    def __init__(self, controller_api: ControllerAPI) -> None:
        self._controller_api = controller_api

    def _get_pv(self, attr_path: list[str], name: str):
        attr_prefix = pv_prefix_from_path(attr_path)
        return f"{attr_prefix}:{snake_to_pascal(name)}"

    def _get_read_widget(self, attribute: Attribute) -> ReadWidgetUnion | None:
        match attribute.datatype:
            case Bool():
                return LED()
            case Int():
                return TextRead(precision=0)
            case Float(prec=precision):
                return TextRead(precision=precision)
            case String():
                return TextRead(format=TextFormat.string)
            case Enum():
                return TextRead(format=TextFormat.string)
            case Waveform() as waveform:
                if len(waveform.shape) > 1:
                    logger.warning(
                        "EPICS CA transport only supports 1D waveforms, "
                        f"{attribute} is a {len(waveform.shape)}D waveform"
                    )
                    return None

                return ArrayTrace(axis="x")
            case datatype:
                raise TypeError(f"Unsupported type {type(datatype)}: {datatype}")

    def _get_write_widget(self, attribute: Attribute) -> WriteWidgetUnion | None:
        match attribute.datatype:
            case Bool():
                return ToggleButton()
            case Int():
                return TextWrite(precision=0)
            case Float(prec=precision):
                return TextWrite(precision=precision)
            case String():
                return TextWrite(format=TextFormat.string)
            case Enum():
                return ComboBox(choices=attribute.datatype.names)
            case Waveform():
                return None
            case datatype:
                raise TypeError(f"Unsupported type {type(datatype)}: {datatype}")

    def _get_attribute_component(
        self, attr_path: list[str], name: str, attribute: Attribute
    ) -> SignalR | SignalW | SignalRW | None:
        pv = self._get_pv(attr_path, name)
        name = snake_to_pascal(name)
        match attribute:
            case AttrRW():
                read_widget = self._get_read_widget(attribute)
                write_widget = self._get_write_widget(attribute)
                if write_widget is None or read_widget is None:
                    return None
                return SignalRW(
                    name=name,
                    description=attribute.description,
                    write_pv=pv,
                    write_widget=write_widget,
                    read_pv=pv + "_RBV",
                    read_widget=read_widget,
                )
            case AttrR():
                read_widget = self._get_read_widget(attribute)
                if read_widget is None:
                    return None
                return SignalR(
                    name=name,
                    description=attribute.description,
                    read_pv=pv,
                    read_widget=read_widget,
                )
            case AttrW():
                write_widget = self._get_write_widget(attribute)
                if write_widget is None:
                    return None
                return SignalW(
                    name=name,
                    description=attribute.description,
                    write_pv=pv,
                    write_widget=write_widget,
                )
            case _:
                raise TypeError(f"Unsupported attribute type: {type(attribute)}")

    def _get_command_component(self, attr_path: list[str], name: str):
        pv = self._get_pv(attr_path, name)
        name = snake_to_pascal(name)

        return SignalX(
            name=name,
            write_pv=pv,
            write_widget=ButtonPanel(actions={name: self.command_value}),
        )

    def build_device(self, title: str) -> Device:
        components = self.extract_api_components(self._controller_api)
        return Device(label=title, children=components)

    def extract_api_components(self, controller_api: ControllerAPI) -> Tree:
        components: Tree = []

        for name, api in controller_api.sub_apis.items():
            if name.isdigit():
                name = f"{controller_api.path[-1]}{name}"
            components.append(
                Group(
                    name=snake_to_pascal(name),
                    label=api.path[-1] if api.path else None,
                    layout=SubScreen(),
                    children=self.extract_api_components(api),
                )
            )

        groups: dict[str, list[ComponentUnion]] = {}
        for attr_name, attribute in controller_api.attributes.items():
            try:
                signal = self._get_attribute_component(
                    controller_api.path,
                    attr_name,
                    attribute,
                )
            except ValidationError as e:
                print(f"Invalid name:\n{e}")
                continue

            if signal is None:
                continue

            match attribute:
                case Attribute(group=group) if group is not None:
                    if group not in groups:
                        groups[group] = []

                    # Remove duplication of group name and signal name
                    signal.name = signal.name.removeprefix(group)

                    groups[group].append(signal)
                case _:
                    components.append(signal)

        for name, command in controller_api.command_methods.items():
            signal = self._get_command_component(controller_api.path, name)

            match command:
                case Command(group=group) if group is not None:
                    if group not in groups:
                        groups[group] = []

                    groups[group].append(signal)
                case _:
                    components.append(signal)

        for name, children in groups.items():
            components.append(Group(name=name, layout=Grid(), children=children))

        return components
