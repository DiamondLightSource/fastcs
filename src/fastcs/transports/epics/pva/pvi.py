from collections import defaultdict
from typing import Literal

from p4p import Type, Value
from p4p.nt.common import alarm, timeStamp
from p4p.server import StaticProvider
from p4p.server.asyncio import SharedPV

from fastcs.controllers import ControllerAPI
from fastcs.util import snake_to_pascal

from .types import p4p_alarm_states, p4p_timestamp_now

AccessModeType = Literal["r", "w", "rw", "d", "x"]


def add_pvi_info(
    provider: StaticProvider,
    pv_prefix: str,
    controller_api: ControllerAPI,
    description: str | None = None,
) -> None:
    """Add PVI information to given provider."""
    provider.add(
        f"{pv_prefix}:PVI",
        SharedPV(initial=_make_p4p_value(pv_prefix, controller_api, description)),
    )


def _make_p4p_value(
    pv_prefix: str, controller_api: ControllerAPI, description: str | None
) -> Value:
    display = (
        {"display": {"description": description}} if description is not None else {}
    )  # Defined here so the value can be (none)

    raw_value = _make_p4p_raw_value(pv_prefix, controller_api)
    p4p_type = _make_type_for_raw_value(raw_value)

    try:
        return Value(
            p4p_type,
            {
                **p4p_alarm_states().model_dump(),
                **p4p_timestamp_now(),
                **display,
                "value": raw_value,
            },
        )
    except KeyError as e:
        raise ValueError(f"Failed to create p4p Value from {raw_value}") from e


def _make_p4p_raw_value(pv_prefix: str, controller_api: ControllerAPI) -> dict:
    p4p_raw_value = defaultdict(dict)
    # Sub-controller api returned if current item is a Controller
    for pv_leaf, sub_controller_api in controller_api.sub_apis.items():
        # Add Controller entry
        pv = f"{pv_prefix}:{snake_to_pascal(pv_leaf)}:PVI"
        if sub_controller_api.path[-1].isdigit():
            # Sub-device of a ControllerVector
            p4p_raw_value[f"__{int(pv_leaf)}"]["d"] = pv
        else:
            p4p_raw_value[pv_leaf.lower()]["d"] = pv
    for pv_leaf, attribute in controller_api.attributes.items():
        # Add attribute entry
        pv = f"{pv_prefix}:{snake_to_pascal(pv_leaf)}"
        match attribute.access_mode:
            case "rw":
                entry = {"r": f"{pv}_RBV", "w": pv}
            case access:
                entry = {access: pv}
        p4p_raw_value[pv_leaf].update(entry)
    for pv_leaf, _ in controller_api.command_methods.items():
        pv = f"{pv_prefix}:{snake_to_pascal(pv_leaf)}"
        p4p_raw_value[pv_leaf]["x"] = pv

    return p4p_raw_value


def _make_type_for_raw_value(raw_value: dict) -> Type:
    p4p_raw_type = []
    for pvi_group_name, access_to_field in raw_value.items():
        pvi_group_structure = []
        for access, field in access_to_field.items():
            if isinstance(field, str):
                pvi_group_structure.append((access, "s"))
            elif isinstance(field, dict):
                pvi_group_structure.append(
                    (
                        access,
                        (
                            "S",
                            None,
                            [(v, "s") for v, _ in field.items()],
                        ),
                    )
                )

        p4p_raw_type.append((pvi_group_name, ("S", "structure", pvi_group_structure)))

    return Type(
        [
            ("alarm", alarm),
            ("timeStamp", timeStamp),
            ("display", ("S", None, [("description", "s")])),
            ("value", ("S", "structure", p4p_raw_type)),
        ]
    )
