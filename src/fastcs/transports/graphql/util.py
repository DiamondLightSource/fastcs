import re

_GRAPHQL_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_graphql_id(id: str) -> None:
    """Reject controller ids that aren't valid GraphQL field names.

    GraphQL ``Name`` syntax is the most restrictive of FastCS's transports:
    only letters, digits and underscores are allowed, and the first character
    cannot be a digit. This drives the lowest-common-denominator id-naming
    guidance for users mixing transports.
    """
    if not id:
        raise ValueError("Controller id is empty; ids must be non-empty")
    if not _GRAPHQL_ID_RE.fullmatch(id):
        raise ValueError(
            f"Controller id {id!r} is not a valid GraphQL id; "
            "must match GraphQL Name syntax (letters, digits, underscores; "
            "no leading digit)"
        )
