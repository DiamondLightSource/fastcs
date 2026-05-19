import re

import pytest

from fastcs.transports.tango.util import (
    tango_dev_class_name,
    tango_dev_name,
    validate_tango_id,
)


class TestValidateTangoId:
    @pytest.mark.parametrize(
        "name",
        ["DEVICE", "DEV-1", "dev_1", "ALPHA", "BENCHMARK-DEVICE", "0LEAD"],
    )
    def test_accepts_valid_ids(self, name: str):
        validate_tango_id(name)

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty"):
            validate_tango_id("")

    @pytest.mark.parametrize(
        "name",
        ["bad/id", "bad id", "bad.id", "bad:id", "bad!id"],
    )
    def test_rejects_illegal_chars(self, name: str):
        with pytest.raises(ValueError, match=re.escape(name)):
            validate_tango_id(name)


class TestTangoDevClassName:
    def test_passes_through_valid_python_identifiers(self):
        assert tango_dev_class_name("DEVICE") == "DEVICE"
        assert tango_dev_class_name("dev_1") == "dev_1"

    def test_replaces_hyphens_with_underscores(self):
        assert tango_dev_class_name("BENCHMARK-DEVICE") == "BENCHMARK_DEVICE"

    def test_prefixes_leading_digit_with_x(self):
        assert tango_dev_class_name("0LEAD") == "X0LEAD"
        assert tango_dev_class_name("1-2") == "X1_2"


class TestTangoDevName:
    def test_three_segments_with_id_leading(self):
        assert tango_dev_name("DEVICE", "INST") == "DEVICE/DEVICE/INST"
        assert (
            tango_dev_name("BENCHMARK-DEVICE", "MY_INST")
            == "BENCHMARK-DEVICE/BENCHMARK_DEVICE/MY_INST"
        )
