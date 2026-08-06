import re

from fastcs.controllers import ControllerAPI
from fastcs.transports.epics.util import validate_epics_pv_id

_PVA_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_pva_id(controller_api: ControllerAPI) -> None:
    """Reject controller ids that wouldn't be safe in an EPICS PVA PV name.

    Rejects ids with characters outside ``[A-Za-z0-9_-]`` and rejects setups
    where the longest derivable PV prefix already exceeds the 60-character
    EPICS PV name limit.
    """
    validate_epics_pv_id(
        controller_api, transport_label="EPICS PVA id", id_re=_PVA_ID_RE
    )
