from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.datatypes import Limits, NumericLimits
from fastcs.transports.epics.pva.types import cast_to_p4p_value

LIMITS = NumericLimits(display=Limits(0.0, 100.0), control=Limits(10.0, 90.0))


def test_p4p_limits():
    readable = cast_to_p4p_value(AttrR(float, limits=LIMITS), 50.0)
    assert "control" not in readable.type().keys()
    assert (readable["display.limitLow"], readable["display.limitHigh"]) == (0, 100)

    writable = cast_to_p4p_value(AttrW(float, limits=LIMITS), 50.0)
    assert "display" not in writable.type().keys()
    assert (writable["control.limitLow"], writable["control.limitHigh"]) == (10, 90)

    both = cast_to_p4p_value(AttrRW(float, limits=LIMITS), 50.0)
    assert (both["display.limitLow"], both["display.limitHigh"]) == (0, 100)
    assert (both["control.limitLow"], both["control.limitHigh"]) == (10, 90)
