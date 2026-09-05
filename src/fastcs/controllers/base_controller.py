from __future__ import annotations

from inspect import getattr_static
from typing import (
    TypeVar,
    get_args,
    get_origin,
)

from fastcs.attributes import Attribute, UnboundAttr
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.controllers.filler import ControllerFiller
from fastcs.methods import Command, Scan, UnboundCommand, UnboundScan
from fastcs.tracer import Tracer

T = TypeVar("T")


def _declared_datatype(hint: object) -> type | None:
    """The datatype an ``AttrR[int]``-style hint names, if it names one."""
    args = get_args(hint)
    return args[0] if len(args) == 1 and isinstance(args[0], type) else None


class BaseController(Tracer):
    """Base class for controllers

    Instances of this class can be loaded into FastCS to expose its Attributes to
    the transport layer, which can then perform a specific function such as generating a
    UI or creating parameters for a control system.

    This class is public for type hinting purposes, but should not be inherited to
    implement device drivers. Use either ``Controller`` or ``ControllerVector`` instead.

    """

    # These class attributes can be overridden on child classes to define default
    # behaviour of instantiated controllers
    root_attribute: Attribute | None = None
    description: str | None = None

    def __init__(
        self,
        path: list[str] | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__()

        if description is not None:
            # Use the argument over the one class defined description.
            self.description = description

        self._path: list[str] = path or []

        # Internal state that should not be accessed directly by base classes
        self.__attributes: dict[str, Attribute] = {}
        self.__sub_controllers: dict[str, BaseController] = {}
        self.__command_methods: dict[str, Command] = {}
        self.__scan_methods: dict[str, Scan] = {}

        self.filler = ControllerFiller(self)
        """Creates and tracks the children this controller's class body declares"""

        self.filler.read_hints()
        self._bind_attrs()
        self.filler.create_children_from_hints()

    @classmethod
    def _walk_mro(cls):
        """Return ordered attribute names from the class MRO.

        Traverses MRO from base to subclass, collecting attribute names in definition
        order while skipping duplicates (subclass overrides take precedence) and private
        attributes.
        """
        class_dir = []
        seen = set()
        for base in reversed(cls.__mro__):
            for key in base.__dict__:
                if not key.startswith("_") and key not in seen:
                    seen.add(key)
                    class_dir.append(key)
        return class_dir

    def _bind_attrs(self) -> None:
        """Bind the class body's declarations to this instance.

        A class body holds declarations and decorated behaviour, never
        `Attribute` instances (ADR 0013), so there is nothing to copy: each
        kind of declaration is built fresh for this instance.

        - an ``@attr``-decorated getter is an `UnboundAttr`, bound into an
          ``AttrR``/``AttrRW`` whose getter and setter are methods of this
          instance
        - a ``@command``/``@scan`` method is bound the same way, so it can be
          called from any context with this instance as ``self``

        Bare type hints are the third kind, and `ControllerFiller` handles
        those separately - they create children rather than binding them.
        """
        for attr_name in dict.fromkeys(self._walk_mro()):
            if attr_name == "root_attribute":
                continue

            # An ``UnboundAttr`` is a descriptor that refuses to be read before
            # it is bound, so reach past it to the declaration itself.
            declaration = getattr_static(self, attr_name, None)
            if isinstance(declaration, UnboundAttr):
                self.add_attribute(attr_name, declaration.bind(self))
                continue

            attr = getattr(self, attr_name, None)
            if isinstance(attr, Attribute):
                raise TypeError(
                    f"{type(self).__name__}.{attr_name} is an "
                    f"{type(attr).__name__} in the class body, which would be "
                    "shared by every instance of this controller. Construct it in "
                    "`__init__`, or declare it as a type hint "
                    f"(`{attr_name}: {type(attr).__name__}[...]`) and let the "
                    "filler create it."
                )
            if isinstance(attr, Command):
                self.add_command(attr_name, attr)
            elif isinstance(attr, Scan):
                self.add_scan(attr_name, attr)
            elif isinstance(
                unbound_command := getattr(attr, "__unbound_command__", None),
                UnboundCommand,
            ):
                self.add_command(attr_name, unbound_command.bind(self))
            elif isinstance(
                unbound_scan := getattr(attr, "__unbound_scan__", None),
                UnboundScan,
            ):
                self.add_scan(attr_name, unbound_scan.bind(self))

    def __repr__(self):
        name = self.__class__.__name__
        path = ".".join(self.path) or None
        sub_controllers = list(self.sub_controllers.keys()) or None

        return f"{name}(path={path}, sub_controllers={sub_controllers})"

    def __setattr__(self, name, value):
        if isinstance(value, Attribute):
            self.add_attribute(name, value)
        elif isinstance(value, Command):
            self.add_command(name, value)
        elif isinstance(value, Scan):
            self.add_scan(name, value)
        elif isinstance(value, BaseController):
            self.add_sub_controller(name, value)
        else:
            super().__setattr__(name, value)

    async def initialise(self):
        """Hook for subclasses to dynamically add attributes before building the API"""
        pass

    def post_initialise(self):
        """Hook to call after all attributes added, before serving the application"""
        self.check_filled()

    def check_filled(self, source: str | None = None):
        """Check that every class-body declaration was provisioned, recursively.

        A driver may call ``self.filler.check_filled(source)`` itself at the
        end of its own ``initialise``, naming where its data came from; the
        framework calls this afterwards so that a controller which forgot to
        does not serve a half-built API.
        """
        self.filler.check_filled(source)

        for sub_controller in self.sub_controllers.values():
            sub_controller.check_filled(source)

    @property
    def path(self) -> list[str]:
        """Path prefix of attributes, recursively including parent Controllers."""
        return self._path

    def set_path(self, path: list[str]):
        if self._path:
            raise ValueError(f"sub controller is already registered under {self.path}")

        self._path = path
        for attribute in self.__attributes.values():
            attribute.set_path(path)

    def _check_for_name_clash(self, name: str):
        namespaces = {
            "attribute": self.__attributes,
            "sub controller": self.__sub_controllers,
            "scan method": self.__scan_methods,
            "command method": self.__command_methods,
        }

        for kind, namespace in namespaces.items():
            if name in namespace:
                raise ValueError(
                    f"Controller {self} has existing {kind} {name}: {namespace[name]}"
                )

    def add_attribute(self, name, attr: Attribute):
        try:
            self._check_for_name_clash(name)
        except ValueError as exc:
            raise ValueError(f"Cannot add attribute {attr}.") from exc

        self._check_against_declaration(name, attr, "attribute", "access mode")

        attr.set_name(name)
        attr.set_path(self.path)
        self.__attributes[name] = attr
        super().__setattr__(name, attr)

    @property
    def attributes(self) -> dict[str, Attribute]:
        return self.__attributes

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        try:
            self._check_for_name_clash(name)
        except ValueError as exc:
            raise ValueError(f"Cannot add sub controller {sub_controller}.") from exc

        self._check_against_declaration(name, sub_controller, "sub controller", "type")

        sub_controller.set_path(self.path + [name])
        self.__sub_controllers[name] = sub_controller
        super().__setattr__(name, sub_controller)

        if isinstance(sub_controller.root_attribute, Attribute):
            self.__attributes[name] = sub_controller.root_attribute

    @property
    def sub_controllers(self) -> dict[str, BaseController]:
        return self.__sub_controllers

    def _check_against_declaration(
        self, name: str, member: object, kind: str, mismatch: str
    ):
        """Check a member being added matches what the class body declared.

        The filler creates what it can from a hint, so what reaches here is
        either introspection satisfying a promise (``state: AttrR``) or a
        driver adding something that clashes with a declaration.
        """
        declaration = self.filler.declarations.get(name)
        if declaration is None:
            return

        expected = get_origin(declaration.hint) or declaration.hint
        if not isinstance(member, expected):
            raise RuntimeError(
                f"Controller '{self.__class__.__name__}' introspection of "
                f"hinted {kind} '{name}' does not match defined {mismatch}. "
                f"Expected '{expected.__name__}' got '{type(member).__name__}'."
            )

        datatype = _declared_datatype(declaration.hint)
        if (
            datatype is not None
            and isinstance(member, Attribute)
            and datatype != member.dtype
        ):
            raise RuntimeError(
                f"Controller '{self.__class__.__name__}' introspection of "
                f"hinted {kind} '{name}' does not match defined datatype. "
                f"Expected '{datatype.__name__}', got '{member.dtype.__name__}'."
            )

    def add_command(self, name: str, command: Command):
        try:
            self._check_for_name_clash(name)
            self._check_against_declaration(name, command, "command method", "type")
        except (ValueError, RuntimeError) as exc:
            raise exc.__class__(f"Cannot add command method {command}.") from exc

        self.__command_methods[name] = command
        super().__setattr__(name, command)

    @property
    def command_methods(self) -> dict[str, Command]:
        return self.__command_methods

    def add_scan(self, name: str, scan: Scan):
        try:
            self._check_for_name_clash(name)
            self._check_against_declaration(name, scan, "scan method", "type")
        except (ValueError, RuntimeError) as exc:
            raise exc.__class__(f"Cannot add scan method {scan}.") from exc

        self.__scan_methods[name] = scan
        super().__setattr__(name, scan)

    @property
    def scan_methods(self) -> dict[str, Scan]:
        return self.__scan_methods

    def _build_api(self, path: list[str]) -> ControllerAPI:
        return ControllerAPI(
            path=path,
            attributes=self.attributes,
            command_methods=self.command_methods,
            scan_methods=self.scan_methods,
            sub_apis={
                name: sub_controller._build_api(path + [name])  # noqa: SLF001
                for name, sub_controller in self.sub_controllers.items()
            },
            description=self.description,
        )
