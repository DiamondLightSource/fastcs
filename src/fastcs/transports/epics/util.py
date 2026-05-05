from fastcs.util import snake_to_pascal


def pv_prefix_from_path(path: list[str]) -> str:
    """Derive an EPICS PV prefix from a controller path.

    The first segment (the controller id) is used verbatim; later segments are
    converted snake_case → PascalCase. Joined with ':'.
    """
    if not path:
        raise ValueError("Cannot derive a PV prefix from an empty path")
    return ":".join([path[0]] + [snake_to_pascal(node) for node in path[1:]])
