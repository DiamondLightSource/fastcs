import re

from fastcs.controllers import ControllerAPI
from fastcs.util import snake_to_pascal

EPICS_MAX_NAME_LENGTH = 60
"""Maximum length of an EPICS PV name, enforced by both CA and PVA transports."""


def pv_prefix_from_path(path: list[str]) -> str:
    """Derive an EPICS PV prefix from a controller path.

    The first segment (the controller id) is used verbatim; later segments are
    converted snake_case → PascalCase. Joined with ':'.
    """
    if not path:
        raise ValueError("Cannot derive a PV prefix from an empty path")
    return ":".join([path[0]] + [snake_to_pascal(node) for node in path[1:]])


def validate_epics_pv_id(
    controller_api: ControllerAPI,
    *,
    transport_label: str,
    id_re: re.Pattern[str],
) -> None:
    """Reject controller ids that wouldn't be safe in an EPICS PV name.

    Rejects ids with characters not matched by ``id_re`` and rejects setups
    where the longest derivable PV prefix already exceeds the
    ``EPICS_MAX_NAME_LENGTH`` PV name limit. ``transport_label`` is the
    transport-specific tag (e.g. ``"EPICS CA id"``) that appears in the
    illegal-characters error message.
    """
    id = controller_api.path[0]
    if not id_re.fullmatch(id):
        raise ValueError(
            f"Controller id {id!r} is not a valid {transport_label}; "
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
