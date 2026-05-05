import re

from fastcs.controllers import ControllerAPI
from fastcs.transports.epics.util import EPICS_MAX_NAME_LENGTH, pv_prefix_from_path

_PVA_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_pva_id(controller_api: ControllerAPI) -> None:
    """Reject controller ids that wouldn't be safe in an EPICS PVA PV name.

    Rejects ids with characters outside ``[A-Za-z0-9_-]`` and rejects setups
    where the longest derivable PV prefix already exceeds the 60-character
    EPICS PV name limit.
    """
    id = controller_api.path[0]
    if not _PVA_ID_RE.fullmatch(id):
        raise ValueError(
            f"Controller id {id!r} is not a valid EPICS PVA id; "
            "only alphanumerics, '-' and '_' are allowed"
        )
    longest_prefix = max(
        len(pv_prefix_from_path(api.path)) for api in controller_api.walk_api()
    )
    if longest_prefix > EPICS_MAX_NAME_LENGTH:
        raise ValueError(
            f"Controller id {id!r} produces a PV prefix of "
            f"{longest_prefix} characters, which exceeds the EPICS "
            f"{EPICS_MAX_NAME_LENGTH}-character PV name limit"
        )
