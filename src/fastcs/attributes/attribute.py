from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Generic, Literal, cast

from fastcs.datatypes import (
    DType_T,
    Meta,
    Table,
    default_value,
    resolve_datatype,
    validate_meta,
    validate_value,
    values_equal,
)
from fastcs.tracer import Tracer

AttributeAccessMode = Literal["r", "w", "rw"]


class Attribute(Generic[DType_T], Tracer, ABC):
    """Base FastCS attribute.

    Instances of this class added to a ``Controller`` will be used by the FastCS class.

    An attribute's datatype is a python type - ``float``, an `enum.Enum`
    subclass, ``Array1D[np.int32]`` - and everything else that describes it
    (precision, units, limits, shape) is metadata, held as a `Meta` typed dict
    on ``attr.meta``.
    """

    def __init__(
        self,
        datatype: Any = None,
        **meta: Any,
    ) -> None:
        super().__init__()

        # Subclasses may infer the datatype from a getter's return annotation or a
        # setter's value annotation and pass the result down; by the time it reaches
        # here it must be resolved.
        if datatype is None:
            raise ValueError(
                "datatype must be given explicitly, or be inferable from the "
                "getter's return annotation or the setter's value annotation"
            )

        dtype, element_type = resolve_datatype(datatype)
        self._meta: Meta = _resolve_meta(datatype, element_type, meta)
        self._dtype: type[DType_T] = dtype  # pyright: ignore[reportAttributeAccessIssue]

        validate_meta(dtype, self._meta)

        self.enabled = True

        # A callback to use when setting the metadata to a different value, for
        # example changing the units on an int.
        self._update_meta_callbacks: list[Callable[[Meta], None]] = []

        # Path and name to be filled in by Controller it is bound to
        self._name = ""
        self._path = []

    @property
    def dtype(self) -> type[DType_T]:
        """The python type this attribute holds."""
        return self._dtype

    @property
    def meta(self) -> Meta:
        """Everything known about this attribute beyond its python type."""
        return self._meta

    @property
    def description(self) -> str | None:
        return self._meta.get("description")

    @property
    def group(self) -> str | None:
        return self._meta.get("group")

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> list[str]:
        return self._path

    @property
    def full_name(self) -> str:
        return ".".join(self._path + [self._name])

    @property
    @abstractmethod
    def access_mode(self) -> AttributeAccessMode:
        """The access mode of this attribute."""
        ...

    def validate(self, value: Any) -> DType_T:
        """Coerce a value to this attribute's datatype and check its metadata.

        Args:
            value: The value to validate

        Returns:
            The validated value

        Raises:
            ValueError: If the value cannot be coerced, or breaks the metadata

        """
        return validate_value(self._dtype, self._meta, value)

    def equal(self, value1: DType_T, value2: DType_T) -> bool:
        """Whether two values of this attribute's datatype are equal."""
        return values_equal(self._dtype, value1, value2)

    def default_value(self) -> DType_T:
        """The value this attribute holds before anything has set one."""
        return default_value(self._dtype, self._meta)

    def add_update_meta_callback(self, callback: Callable[[Meta], None]) -> None:
        self._update_meta_callbacks.append(callback)

    def update_meta(self, meta: Meta) -> None:
        """Replace this attribute's metadata, notifying anything serving it.

        Args:
            meta: The new metadata, which must be valid for the datatype

        Raises:
            TypeError: If a field is not meaningful for the datatype

        """
        validate_meta(self._dtype, meta, self.full_name or "attribute")

        self._meta = meta
        for callback in self._update_meta_callbacks:
            callback(meta)

    def set_name(self, name: str):
        if self._name:
            raise RuntimeError(
                f"Attribute is already registered with a controller as {self._name}"
            )

        self._name = name

    def set_path(self, path: list[str]):
        if self._path:
            raise RuntimeError(
                f"Attribute is already registered with a controller at {self._path}"
            )

        self._path = path

    def __repr__(self):
        name = self.__class__.__name__
        full_name = self.full_name or None

        return f"{name}(name={full_name}, dtype={self._dtype.__name__})"


def _resolve_meta(
    datatype: Any,
    element_type: Any,
    meta: dict[str, Any],
) -> Meta:
    """Fold what the datatype spelling implied into the metadata given."""
    resolved: dict[str, Any] = {k: v for k, v in meta.items() if v is not None}

    spelled_as_table = isinstance(datatype, type) and issubclass(datatype, Table)
    if spelled_as_table and "structured_dtype" not in resolved:
        raise TypeError(
            "A Table attribute needs its columns - pass "
            "structured_dtype=[('name', np.int32), ...]"
        )
    if not spelled_as_table and "structured_dtype" in resolved:
        raise TypeError(
            "structured_dtype is only valid for a Table attribute; declare the "
            "datatype as Table to use it"
        )

    if element_type is not None:
        # ``Array1D[np.int32]`` says the element type; an explicit array_dtype
        # would be a second, possibly contradictory, source for it.
        if "array_dtype" in resolved:
            raise TypeError(
                "The element type is already given by the datatype subscript; "
                "drop the array_dtype argument"
            )
        resolved["array_dtype"] = element_type

    return cast(Meta, resolved)
