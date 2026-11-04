import pytest
from p4p import Value

from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.datatypes import Limits, NumericLimits
from fastcs.transports.epics.pva.types import cast_to_p4p_value

LIMITS = NumericLimits(display=Limits(0.0, 100.0), control=Limits(10.0, 90.0))
ALARMS = NumericLimits(alarm=Limits(0.0, 100.0), warning=Limits(10.0, 90.0))
ALARM_ONLY = NumericLimits(alarm=Limits(0.0, 100.0))


def _p4p_value(attribute: Attribute, value: float = 50.0) -> Value:
    p4p_value = cast_to_p4p_value(attribute, value)
    assert isinstance(p4p_value, Value)
    return p4p_value


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


@pytest.mark.parametrize(
    "limits, value, severity, message",
    [
        pytest.param(ALARMS, 90.0, 0, "No alarm", id="on-limit-is-inside"),
        pytest.param(
            ALARMS, 95.0, 1, "Above maximum warning limit: 90.0", id="high-warning"
        ),
        pytest.param(
            ALARMS, 105.0, 2, "Above maximum alarm limit: 100.0", id="high-alarm"
        ),
        pytest.param(
            ALARMS, 5.0, 1, "Below minimum warning limit: 10.0", id="low-warning"
        ),
        pytest.param(ALARMS, -1.0, 2, "Below minimum alarm limit: 0.0", id="low-alarm"),
        pytest.param(
            ALARM_ONLY, 105.0, 2, "Above maximum alarm limit: 100.0", id="alarm-only"
        ),
    ],
)
def test_p4p_alarms(limits, value, severity, message):
    state = _p4p_value(AttrR(float, limits=limits), value)["alarm"]

    assert (state["severity"], state["message"]) == (severity, message)


def test_p4p_value_alarm():
    value_alarm = _p4p_value(AttrR(float, limits=ALARMS)).todict()["valueAlarm"]
    expected = {
        "active": True,
        "lowAlarmLimit": 0.0,
        "lowWarningLimit": 10.0,
        "highWarningLimit": 90.0,
        "highAlarmLimit": 100.0,
        "lowAlarmSeverity": 2,
        "lowWarningSeverity": 1,
        "highWarningSeverity": 1,
        "highAlarmSeverity": 2,
    }
    assert {field: value_alarm[field] for field in expected} == expected
    assert _p4p_value(AttrR(float))["valueAlarm.active"] is False
