from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Generic, Literal

from fastcs.attributes.attribute_io_ref import AttributeIORefT
from fastcs.datatypes import DataType, DType, DType_T
from fastcs.tracer import Tracer

AttributeAccessMode = Literal["r", "w", "rw"]


class Attribute(Generic[DType_T, AttributeIORefT], Tracer, ABC):
    """Base FastCS attribute.

    Instances of this class added to a ``Controller`` will be used by the FastCS class.
    """

    def __init__(
        self,
        datatype: DataType[DType_T],
        io_ref: AttributeIORefT | None = None,
        group: str | None = None,
        description: str | None = None,
    ) -> None:
        super().__init__()

        assert issubclass(datatype.dtype, DType), (
            f"Attr type must be one of {DType}, received type {datatype.dtype}"
        )
        self._io_ref = io_ref
        self._datatype: DataType[DType_T] = datatype
        self._group = group
        self.enabled = True
        self.description = description

        # A callback to use when setting the datatype to a different value, for example
        # changing the units on an int.
        self._update_datatype_callbacks: list[Callable[[DataType[DType_T]], None]] = []

        # Path and name to be filled in by Controller it is bound to
        self._name = ""
        self._path = []

    @property
    def io_ref(self) -> AttributeIORefT:
        if self._io_ref is None:
            raise RuntimeError(f"{self} has no AttributeIORef")
        return self._io_ref

    def has_io_ref(self):
        return self._io_ref is not None

    @property
    def datatype(self) -> DataType[DType_T]:
        return self._datatype

    @property
    def dtype(self) -> type[DType_T]:
        return self._datatype.dtype

    @property
    def group(self) -> str | None:
        return self._group

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

    def add_update_datatype_callback(
        self, callback: Callable[[DataType[DType_T]], None]
    ) -> None:
        self._update_datatype_callbacks.append(callback)

    def update_datatype(self, datatype: DataType[DType_T]) -> None:
        if not isinstance(self._datatype, type(datatype)):
            raise ValueError(
                f"Attribute datatype must be of type {type(self._datatype)}"
            )
        self._datatype = datatype
        for callback in self._update_datatype_callbacks:
            callback(datatype)

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
        datatype = self._datatype.__class__.__name__

        return f"{name}(name={full_name}, datatype={datatype}, io_ref={self._io_ref})"
