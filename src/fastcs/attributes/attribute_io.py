from typing import Any, Generic, cast, get_args

from fastcs.attributes.attr_r import AttrR
from fastcs.attributes.attr_w import AttrW
from fastcs.attributes.attribute_io_ref import AttributeIORef, AttributeIORefT
from fastcs.datatypes import DType_T
from fastcs.tracer import Tracer


class AttributeIO(Generic[DType_T, AttributeIORefT], Tracer):
    """Base class for performing IO for an `Attribute`

    This class should be inherited to implement reading and writing values from
    ``Attributes`` via some API. For read, ``Attribute``s implement the ``update``
    method and for write, ``Attribute`` implement the ``send`` method.

    Concrete implementations of this class must be parameterised with a specific
    ``AttributeIORef`` that defines exactly what part of the API the ``Attribute``
    corresponds to. See the docstring for `AttributeIORef` for more information.
    """

    ref_type = AttributeIORef

    def __init_subclass__(cls) -> None:
        # sets ref_type from subclass generic args
        # from python 3.12 we can use types.get_original_bases
        args = get_args(cast(Any, cls).__orig_bases__[0])
        cls.ref_type = args[1]

    def __init__(self):
        super().__init__()

    async def update(self, attr: AttrR[DType_T, AttributeIORefT]) -> None:
        """Update `AttrR` value from device

        This method will be  called in `AttrR.update` in a background task.

        Exceptions raised by this method will be caught and logged with a full stack
        trace. If using targeted try-except blocks to log more specific errors, this
        should be done with stack trace and exceptions should be re-raised to be handled
        by FastCS.

        """
        raise NotImplementedError()

    async def send(self, attr: AttrW[DType_T, AttributeIORefT], value: DType_T) -> None:
        """Send `Attribute` value to device

        This method will be called in `AttrW.put`, generally from a `Transport`.

        Exceptions raised by this method will be caught and logged with a full stack
        trace. If using targetted try-except blocks to log more specific errors, this
        should be done with stack trace and exceptions should be re-raised to be handled
        by FastCS..

        """
        raise NotImplementedError()


AnyAttributeIO = AttributeIO[Any]
