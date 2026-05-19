import pytest

from fastcs.controllers import ControllerAPI
from fastcs.transports.epics.pva.util import validate_pva_id


@pytest.mark.parametrize("name", ["DEVICE", "my-id", "name_1", "ABC-123_xyz"])
def test_validate_pva_id_accepts_valid(name):
    validate_pva_id(ControllerAPI(path=[name]))


@pytest.mark.parametrize("name", ["bad/id", "with space", "colons:in:id", ""])
def test_validate_pva_id_rejects_illegal_characters(name):
    with pytest.raises(ValueError, match="EPICS PVA id"):
        validate_pva_id(ControllerAPI(path=[name]))


def test_validate_pva_id_rejects_overlong_prefix():
    deep_path = ["A" * 50, "deeper_sub_controller_path"]
    with pytest.raises(ValueError, match="exceeds the EPICS"):
        validate_pva_id(
            ControllerAPI(
                path=deep_path[:1],
                sub_apis={"sub": ControllerAPI(path=deep_path)},
            )
        )
