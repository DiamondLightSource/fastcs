"""The python types a FastCS `Attribute` can hold, and how they are spelled.

There is no ``DataType`` object: an attribute's datatype *is* a python type,
and everything that used to hang off a ``DataType`` instance - precision,
units, limits, array shape - now travels separately as a ``*Meta`` typed dict
(see :py:mod:`fastcs.datatypes.meta`).
"""

from __future__ import annotations

import enum
from typing import Any, TypeAlias, TypeVar, get_args, get_origin

import numpy as np
from numpy.typing import DTypeLike

DType = (
    int  # int
    | float  # float
    | bool  # bool
    | str  # str
    | enum.Enum  # any Enum subclass
    | np.ndarray  # Array1D / Table
)
"""A python type that a FastCS `Attribute` can hold"""

DType_T = TypeVar("DType_T", bound=DType)
"""A TypeVar of `DType` for use in generic classes and functions"""

NumpyScalar_T = TypeVar("NumpyScalar_T", bound=np.generic, covariant=True)
"""The element type of a numpy array"""

Array1D: TypeAlias = np.ndarray[tuple[int], np.dtype[NumpyScalar_T]]
"""A one dimensional numpy array, subscripted with its element type.

``Array1D[np.int32]`` is both the type hint for an array attribute and the
datatype passed to its constructor - the element type is read straight off the
subscript, so it does not have to be repeated in the metadata::

    AttrR(Array1D[np.int32], shape=(10,))

Arrays of higher rank have no ophyd-async-compatible spelling; write them as
``np.ndarray`` with an explicit ``array_dtype``::

    AttrR(np.ndarray, array_dtype=np.int32, shape=(10, 10))
"""


class Table(np.ndarray):
    """A structured ("record") numpy array, one field per column.

    Both the type hint and the datatype for a table attribute; the columns are
    given as the ``structured_dtype`` metadata::

        AttrR(Table, structured_dtype=[("index", np.int32), ("value", np.float64)])

    See https://numpy.org/devdocs/user/basics.rec.html for structured dtypes.
    """


_BUILTIN_DTYPES: tuple[type, ...] = (bool, int, float, str)
"""The builtin types an attribute may hold, matched exactly rather than by
subclass - ``bool`` is a subclass of ``int``, and the two are not
interchangeable to a transport."""


def is_array_datatype(dtype: type[DType]) -> bool:
    """Whether ``dtype`` is held as a numpy array - an `Array1D` or a `Table`."""
    return issubclass(dtype, np.ndarray)


def resolve_datatype(datatype: Any) -> tuple[type[DType], DTypeLike | None]:
    """Resolve a datatype as written into the python type an attribute holds.

    Args:
        datatype: A datatype spelling - a builtin type, an `enum.Enum`
            subclass, `Table`, ``np.ndarray``, or a subscripted `Array1D`

    Returns:
        The python type, and the numpy element type carried by the spelling if
        it had one (``Array1D[np.int32]`` carries ``np.int32``; a bare
        ``np.ndarray`` carries nothing and needs an ``array_dtype``)

    Raises:
        TypeError: If ``datatype`` is not a supported spelling

    """
    # ``Array1D[np.int32]`` is a subscripted generic alias rather than a class.
    if (origin := get_origin(datatype)) is not None:
        if not (isinstance(origin, type) and issubclass(origin, np.ndarray)):
            raise TypeError(f"Unsupported datatype {datatype!r}")

        return np.ndarray, _element_type_of(datatype)

    if not isinstance(datatype, type):
        raise TypeError(
            f"Datatype must be a type, got {datatype!r}. Metadata such as "
            "precision or units is passed as keyword arguments, not as part "
            "of the datatype."
        )

    if datatype in _BUILTIN_DTYPES or issubclass(datatype, enum.Enum):
        return datatype, None

    if issubclass(datatype, np.ndarray):
        # ``Table`` and ``Array1D`` are both held as plain ``np.ndarray``; what
        # separates them is whether the metadata gives a structured dtype.
        return np.ndarray, None

    raise TypeError(f"Unsupported datatype {datatype!r}")


def _element_type_of(alias: Any) -> DTypeLike | None:
    """The numpy element type of a subscripted ``np.ndarray`` alias, if given."""
    args = get_args(alias)
    if len(args) != 2:
        return None

    # ``np.ndarray[tuple[int], np.dtype[np.int32]]`` - the element type is the
    # argument of the inner ``np.dtype``.
    dtype_args = get_args(args[1])

    return dtype_args[0] if dtype_args else None


Enum_T = TypeVar("Enum_T", bound=enum.Enum)
"""A TypeVar of any `enum.Enum` subclass an attribute can hold"""

Array_T = TypeVar("Array_T", bound=np.ndarray)
"""A TypeVar of any numpy array an attribute can hold"""


Inferred_T = TypeVar("Inferred_T", bound=DType)
"""A TypeVar of `DType` for the constructor overload that infers the datatype

Distinct from `DType_T` because the overload binds it from the getter or setter
in the same signature that annotates ``self``, and a class-scoped TypeVar cannot
be used there.
"""
