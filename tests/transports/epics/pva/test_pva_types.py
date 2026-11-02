from p4p import Value

from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.datatypes import Limits, NumericLimits
from fastcs.transports.epics.pva.types import cast_to_p4p_value

LIMITS = NumericLimits(display=Limits(0.0, 100.0), control=Limits(10.0, 90.0))


def _p4p_value(attribute: Attribute) -> Value:
    value = cast_to_p4p_value(attribute, 50.0)
    assert isinstance(value, Value)
    return value


def test_p4p_limits():
    readable = _p4p_value(AttrR(float, limits=LIMITS))
    assert "control" not in readable.type().keys()
    assert (readable["display.limitLow"], readable["display.limitHigh"]) == (0, 100)

    writable = _p4p_value(AttrW(float, limits=LIMITS))
    assert "display" not in writable.type().keys()
    assert (writable["control.limitLow"], writable["control.limitHigh"]) == (10, 90)

    both = _p4p_value(AttrRW(float, limits=LIMITS))
    assert (both["display.limitLow"], both["display.limitHigh"]) == (0, 100)
    assert (both["control.limitLow"], both["control.limitHigh"]) == (10, 90)
