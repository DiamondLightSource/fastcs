from __future__ import annotations

from copy import deepcopy
from typing import (
    TypeVar,
    _GenericAlias,  # type: ignore
    get_args,
    get_origin,
    get_type_hints,
)

from fastcs.attributes import Attribute, HintedAttribute
from fastcs.controllers.controller_api import ControllerAPI
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

        self.__hinted_attributes: dict[str, HintedAttribute] = {}
        self.__hinted_methods: dict[str, type[Method]] = {}
        self.__hinted_sub_controllers: dict[str, type[BaseController]] = {}
        self._find_type_hints()

        self._bind_attrs()

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
                    if len(args) == 1:
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
        self._validate_type_hints()

    def _validate_type_hints(self):
        """Validate all type-hints were introspected"""
        for name in self.__hinted_attributes:
            self._validate_hinted_attribute(name)

        for name in self.__hinted_sub_controllers:
            self._validate_hinted_controller(name)

        for name in self.__hinted_methods:
            self._validate_hinted_method(name)

        for subcontroller in self.sub_controllers.values():
            subcontroller._validate_type_hints()  # noqa: SLF001

    def _validate_hinted_member(self, name: str, expected_type: type[T]) -> T:
        """Validate that a hinted member exists on the controller"""
        member = getattr(self, name, None)
        if member is None or not isinstance(member, expected_type):
            raise RuntimeError()
        return member

    def _validate_hinted_method(self, name: str):
        """Check that a `Method` with the given name exists on the controller"""
        try:
            method = self._validate_hinted_member(name, Method)
        except RuntimeError:
            raise RuntimeError(
                f"Controller `{self.__class__.__name__}` failed to introspect "
                f"hinted method `{name}` during initialisation"
            ) from None

        logger.debug(
            "Validated hinted method", name=name, controller=self, method=method
        )

    def _validate_hinted_attribute(self, name: str):
        """Check that an `Attribute` with the given name exists on the controller"""
        try:
            attr = self._validate_hinted_member(name, Attribute)
        except RuntimeError:
            raise RuntimeError(
                f"Controller `{self.__class__.__name__}` failed to introspect "
                f"hinted attribute `{name}` during initialisation"
            ) from None

        logger.debug(
            "Validated hinted attribute", name=name, controller=self, attribute=attr
        )

    def _validate_hinted_controller(self, name: str):
        """Check that a sub controller with the given name exists on the controller"""
        try:
            controller = self._validate_hinted_member(name, BaseController)
        except RuntimeError:
            raise RuntimeError(
                f"Controller `{self.__class__.__name__}` failed to introspect "
                f"hinted controller `{name}` during initialisation"
            ) from None

        logger.debug(
            "Validated hinted sub controller",
            name=name,
            controller=self,
            sub_controller=controller,
        )

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
        try:
            self._check_for_name_clash(name)
        except ValueError as exc:
            raise ValueError(f"Cannot add sub controller {sub_controller}.") from exc

        if name in self.__hinted_sub_controllers:
            hint = self.__hinted_sub_controllers[name]
            if not isinstance(sub_controller, hint):
                raise RuntimeError(
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted sub controller '{name}' does not match defined type. "
                    f"Expected '{hint.__name__}' got "
                    f"'{sub_controller.__class__.__name__}'."
                )

        sub_controller.set_path(self.path + [name])
        self.__sub_controllers[name] = sub_controller
        super().__setattr__(name, sub_controller)

        if isinstance(sub_controller.root_attribute, Attribute):
            self.__attributes[name] = sub_controller.root_attribute

    @property
    def sub_controllers(self) -> dict[str, BaseController]:
        return self.__sub_controllers

    def _validate_method(self, name: str, method: Method):
        if name in self.__hinted_methods:
            hint = self.__hinted_methods[name]
            if not isinstance(method, hint):
                raise RuntimeError(
                    f"Controller '{self.__class__.__name__}' introspection of "
                    f"hinted method '{name}' does not match defined type. "
                    f"Expected '{hint.__name__}' got "
                    f"'{method.__class__.__name__}'."
                )

    def add_command(self, name: str, command: Command):
        try:
            self._check_for_name_clash(name)
            self._validate_method(name, command)
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
            self._validate_method(name, scan)
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
