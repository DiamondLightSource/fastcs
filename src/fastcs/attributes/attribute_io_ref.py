from dataclasses import KW_ONLY, dataclass

from typing_extensions import TypeVar


@dataclass
class AttributeIORef:
    """Base for references to define IO for an ``Attribute`` over an API.

    This object acts as a specification of the API that its corresponding
    ``AttributeIO`` should access for a given ``Attribute``. The fields necessary to
    distinguish between different ``Attributes`` is an implementation detail of the IO,
    but some examples are a string to send over a TCP port, or URI within an HTTP
    server.
    """

    # Make fields keyword-only so that child classes can have fields without defaults
    _: KW_ONLY
    update_period: float | None = None
    """Period in seconds between attribute updates, or `ONCE`"""


AttributeIORefT = TypeVar(
    "AttributeIORefT", bound=AttributeIORef, default=AttributeIORef, covariant=True
)
"""An `AttributeIORef` for an `Attribute`"""
