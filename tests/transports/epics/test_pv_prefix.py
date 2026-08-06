import pytest

from fastcs.transports.epics.util import pv_prefix_from_path


def test_pv_prefix_single_segment_verbatim():
    assert pv_prefix_from_path(["my-id"]) == "my-id"


def test_pv_prefix_keeps_root_verbatim_and_pascals_remainder():
    assert pv_prefix_from_path(["my-id", "sub_widget"]) == "my-id:SubWidget"


def test_pv_prefix_pascals_every_non_root_segment():
    assert (
        pv_prefix_from_path(["root_id", "sub_widget", "inner_thing"])
        == "root_id:SubWidget:InnerThing"
    )


def test_pv_prefix_empty_path_raises():
    with pytest.raises(ValueError, match="empty"):
        pv_prefix_from_path([])
