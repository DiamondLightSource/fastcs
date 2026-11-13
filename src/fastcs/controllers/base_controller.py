from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from copy import deepcopy
from typing import (
    TypeVar,
    _GenericAlias,  # type: ignore
    get_args,
    get_origin,
    get_type_hints,
)

from fastcs.attributes import AnyAttributeIO, Attribute, AttrR, AttrW, HintedAttribute
from fastcs.controllers.controller_api import ControllerAPI
from fastcs.datatypes import Bool
from fastcs.logging import logger
from fastcs.methods import Command, Method, Scan, UnboundCommand, UnboundScan
from fastcs.tracer import Tracer

T = TypeVar("T")


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
    # Added to every controller by the framework in __init__
    connected: AttrR[bool]
    description: str | None = None

    _sealed: bool = False
    _registered: bool = False
    # Whether the name dicts exist, so assignments can be checked against them
    _names_ready: bool = False

    def __init__(
        self,
        path: list[str] | None = None,
        description: str | None = None,
        ios: Sequence[AnyAttributeIO] | None = None,
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
        self._names_ready = True

        self.__hinted_attributes: dict[str, HintedAttribute] = {}
        self.__hinted_methods: dict[str, type[Method]] = {}
        self.__hinted_sub_controllers: dict[str, type[BaseController]] = {}
        self._find_type_hints()

        self._bind_attrs()

        # Added to every controller by the framework, so drivers can't use the name
        self.add_attribute(
            "connected",
            AttrR(
                Bool(),
                initial_value=True,
                description="Whether its connections are up",
            ),
        )

        ios = ios or []
        self._attribute_ref_io_map = {io.ref_type: io for io in ios}
        self._validate_io(ios)

    def _find_type_hints(self):
        """Find `Attribute` and `Controller` type hints for introspection validation"""
        for name, hint in get_type_hints(type(self)).items():
            if isinstance(hint, _GenericAlias):  # e.g. AttrR[int]
                args = get_args(hint)
                hint = get_origin(hint)
            else:
                args = None

            if isinstance(hint, type) and issubclass(hint, Attribute):
                if args is None:
                    dtype = None
                else:
                    if len(args) == 2:
                        dtype = args[0]
                    else:
                        raise TypeError(
                            f"Invalid type hint for attribute {name}: {hint}"
                        )

                self.__hinted_attributes[name] = HintedAttribute(
                    attr_type=hint, dtype=dtype
                )

            elif isinstance(hint, type) and issubclass(hint, BaseController):
                self.__hinted_sub_controllers[name] = hint

            elif isinstance(hint, type) and issubclass(hint, Method):
                self.__hinted_methods[name] = hint

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
        """Bind Attributes and Methods to this instance.

        This method will bind the attributes of this controller class to
        this specific instance. For Attributes, this is just a case of copying and
        re-assigning to ``self`` to make it unique across multiple instances of this
        controller class. For Methods, this requires creating a bound method from a
        class method and a controller instance, so that it can be called from any
        context with the controller instance passed as the ``self`` argument.

        """
        class_dir = dict.fromkeys(self._walk_mro())
        class_type_hints = {
            key: value
            for key, value in get_type_hints(type(self)).items()
            if not key.startswith("_")
        }

        for attr_name in {**class_dir, **class_type_hints}:
            if attr_name == "root_attribute":
                continue

            attr = getattr(self, attr_name, None)
            if isinstance(attr, Attribute):
                setattr(self, attr_name, deepcopy(attr))
            else:
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

    def _validate_io(self, ios: Sequence[AnyAttributeIO]):
        """Validate that there is exactly one AttributeIO class registered to the
        controller for each type of AttributeIORef belonging to the attributes of the
        controller"""
        for ref_type, count in Counter([io.ref_type for io in ios]).items():
            if count > 1:
                raise RuntimeError(
                    f"More than one AttributeIO class handles {ref_type.__name__}"
                )

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
            if self._names_ready:
                self._check_name_free(name, "value", value)
            super().__setattr__(name, value)

    async def setup(self):
        """Hook called by the framework after all attributes are added, before serving
        the application"""
        pass

    def seal(self):
        """Validate and connect this controller, then stop anything being added to it.
        Called by the framework on every controller in the tree after construction.

        Raises:
            RuntimeError: If the controller is already sealed

        """
        if self._sealed:
            raise RuntimeError(f"{self} is already sealed")

        self._validate_type_hints()
        self._connect_attribute_ios()
        self._sealed = True

    def _validate_type_hints(self):
        """Validate all type-hints were introspected"""
        for name in self.__hinted_attributes:
            self._validate_hinted_attribute(name)

        for name in self.__hinted_sub_controllers:
            self._validate_hinted_controller(name)

        for name in self.__hinted_methods:
            self._validate_hinted_method(name)

    def _validate_hinted_member(
        self, name: str, kind: str, expected_type: type[T]
    ) -> T:
        """Validate that a hinted member exists on the controller

        Raises:
            RuntimeError: If no member of the expected type has the given name

        """
        member = getattr(self, name, None)
        if member is None or not isinstance(member, expected_type):
            raise RuntimeError(
                f"Controller `{self.__class__.__name__}` failed to introspect "
                f"hinted {kind} `{name}` during initialisation"
            )
        return member

    def _validate_hinted_method(self, name: str):
        """Check that a `Method` with the given name exists on the controller"""
        method = self._validate_hinted_member(name, "method", Method)

        logger.debug(
            "Validated hinted method", name=name, controller=self, method=method
        )

    def _validate_hinted_attribute(self, name: str):
        """Check that an `Attribute` with the given name exists on the controller"""
        attr = self._validate_hinted_member(name, "attribute", Attribute)

        logger.debug(
            "Validated hinted attribute", name=name, controller=self, attribute=attr
        )

    def _validate_hinted_controller(self, name: str):
        """Check that a sub controller with the given name exists on the controller"""
        controller = self._validate_hinted_member(name, "controller", BaseController)

        logger.debug(
            "Validated hinted sub controller",
            name=name,
            controller=self,
            sub_controller=controller,
        )

    def _connect_attribute_ios(self) -> None:
        """Connect ``Attribute`` callbacks to ``AttributeIO``s"""
        for attr in self.__attributes.values():
            ref = attr.io_ref if attr.has_io_ref() else None
            if ref is None:
                continue

            io = self._attribute_ref_io_map.get(type(ref))
            if io is None:
                raise ValueError(
                    f"{self.__class__.__name__} does not have an AttributeIO "
                    f"to handle {attr.io_ref.__class__.__name__}"
                )

            if isinstance(attr, AttrW):
                attr.set_on_put_callback(io.send)
            if isinstance(attr, AttrR):
                attr.set_update_callback(io.update)

    @property
    def path(self) -> list[str]:
        """Path prefix of attributes, recursively including parent Controllers."""
        return self._path

    def set_path(self, path: list[str]):
        """Set the path of this controller, its attributes and its sub controllers.

        Args:
            path: The path of this controller

        Raises:
            ValueError: If the path is already set

        """
        if self._path:
            raise ValueError(f"{self} already has path {self.path}")

        self._path = path
        for attribute in self.__attributes.values():
            attribute.set_path(path)
        for name, sub_controller in self.__sub_controllers.items():
            sub_controller.set_path(path + [name])

    def _check_can_add(self, name: str, kind: str, member: object):
        """Check that a member can be added to this controller under ``name``.

        Raises:
            RuntimeError: If the controller has been sealed
            ValueError: If ``name`` is already used by another member

        """
        if self._sealed:
            raise RuntimeError(
                f"Cannot add {kind} {name} to {self} after it has been sealed"
            )
        self._check_name_free(name, kind, member)

    def _check_name_free(self, name: str, kind: str, member: object):
        """Check that ``name`` is not already used by a member of this controller.

        Raises:
            ValueError: If ``name`` is already used by another member

        """
        namespaces = {
            "attribute": self.__attributes,
            "sub controller": self.__sub_controllers,
            "scan method": self.__scan_methods,
            "command method": self.__command_methods,
        }
        for existing_kind, namespace in namespaces.items():
            if name in namespace:
                raise ValueError(
                    f"Cannot add {kind} {member}. Controller {self} has existing "
                    f"{existing_kind} {name}: {namespace[name]}"
                )

    def add_attribute(self, name, attr: Attribute):
        self._check_can_add(name, "attribute", attr)

        if name in self.__hinted_attributes:
            hint = self.__hinted_attributes[name]
            if not isinstance(attr, hint.attr_type):
                raise RuntimeError(
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted attribute '{name}' does not match defined access mode. "
                    f"Expected '{hint.attr_type.__name__}' got '{type(attr).__name__}'."
                )
            if hint.dtype is not None and hint.dtype != attr.datatype.dtype:
                raise RuntimeError(
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted attribute '{name}' does not match defined datatype. "
                    f"Expected '{hint.dtype.__name__}', "
                    f"got '{attr.datatype.dtype.__name__}'."
                )

        attr.set_name(name)
        attr.set_path(self.path)
        self.__attributes[name] = attr
        super().__setattr__(name, attr)

    @property
    def attributes(self) -> dict[str, Attribute]:
        return self.__attributes

    def add_sub_controller(self, name: str, sub_controller: BaseController):
        self._check_can_add(name, "sub controller", sub_controller)

        if name in self.__hinted_sub_controllers:
            hint = self.__hinted_sub_controllers[name]
            if not isinstance(sub_controller, hint):
                raise RuntimeError(
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted sub controller '{name}' does not match defined type. "
                    f"Expected '{hint.__name__}' got "
                    f"'{sub_controller.__class__.__name__}'."
                )

        if sub_controller._registered:  # noqa: SLF001
            raise ValueError(f"{sub_controller} is already registered")
        sub_controller._registered = True  # noqa: SLF001

        if self.path:
            sub_controller.set_path(self.path + [name])
        self.__sub_controllers[name] = sub_controller
        super().__setattr__(name, sub_controller)

        if isinstance(sub_controller.root_attribute, Attribute):
            self.__attributes[name] = sub_controller.root_attribute

    @property
    def sub_controllers(self) -> dict[str, BaseController]:
        return self.__sub_controllers

    def _validate_method(self, name: str, kind: str, method: Method):
        if name in self.__hinted_methods:
            hint = self.__hinted_methods[name]
            if not isinstance(method, hint):
                raise RuntimeError(
                    f"Cannot add {kind} {method}. "
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted method '{name}' does not match defined type. "
                    f"Expected '{hint.__name__}' got "
                    f"'{method.__class__.__name__}'."
                )

    def add_command(self, name: str, command: Command):
        self._check_can_add(name, "command method", command)
        self._validate_method(name, "command method", command)

        self.__command_methods[name] = command
        super().__setattr__(name, command)

    @property
    def command_methods(self) -> dict[str, Command]:
        return self.__command_methods

    def add_scan(self, name: str, scan: Scan):
        self._check_can_add(name, "scan method", scan)
        self._validate_method(name, "scan method", scan)

        self.__scan_methods[name] = scan
        super().__setattr__(name, scan)

    @property
    def scan_methods(self) -> dict[str, Scan]:
        return self.__scan_methods

    def build_api(self, path: list[str]) -> ControllerAPI:
        return ControllerAPI(
            path=path,
            attributes=self.attributes,
            command_methods=self.command_methods,
            scan_methods=self.scan_methods,
            sub_apis={
                name: sub_controller.build_api(path + [name])
                for name, sub_controller in self.sub_controllers.items()
            },
            description=self.description,
        )
