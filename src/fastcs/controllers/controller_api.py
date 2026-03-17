from collections.abc import Iterator
from dataclasses import dataclass, field

from fastcs.attributes import Attribute
from fastcs.methods import Command, Scan


@dataclass
class ControllerAPI:
    """Attributes, Methods and sub APIs of a Controller to expose in a Transport

    This provides a view of the `Attribute` s, `Method` s of a `Controller` - and its
    sub controllers - to the `Transport` layer, without direct access to its internal
    state.
    """

    path: list[str] = field(default_factory=list)
    """Path within controller tree (empty if this is the root)"""
    attributes: dict[str, Attribute] = field(default_factory=dict)
    """The `Attribute` s from the `Controller`"""
    command_methods: dict[str, Command] = field(default_factory=dict)
    """The `Command` s from the `Controller`"""
    scan_methods: dict[str, Scan] = field(default_factory=dict)
    """The `Scan` s from the `Controller`"""
    sub_apis: dict[str, "ControllerAPI"] = field(default_factory=dict)
    """The `ControllerAPI` s of the sub controllers of the `Controller`"""
    description: str | None = None
    """A description to display in the `Transport` layer"""

    def walk_api(self) -> Iterator["ControllerAPI"]:
        """Walk through all the nested `ControllerAPI` s of this `ControllerAPI`.

        Yields the `ControllerAPI` s from a depth-first traversal of the tree,
        including ``self``.

        """
        yield self
        for api in self.sub_apis.values():
            yield from api.walk_api()

    def __repr__(self):
        return (
            f"ControllerAPI("
            f"path={self.path}, "
            f"sub_apis=[{', '.join(self.sub_apis.keys())}]"
            f")"
        )
