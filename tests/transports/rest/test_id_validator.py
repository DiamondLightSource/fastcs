import pytest

from fastcs.transports.rest.util import validate_rest_id


def test_validate_rest_id_accepts_alnum_dash_underscore():
    validate_rest_id("my-id_42")  # should not raise


def test_validate_rest_id_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        validate_rest_id("")


def test_validate_rest_id_rejects_path_separator():
    with pytest.raises(ValueError, match="bad/id"):
        validate_rest_id("bad/id")


def test_validate_rest_id_rejects_space():
    with pytest.raises(ValueError, match="bad id"):
        validate_rest_id("bad id")
