from multiprocessing import Queue

from p4p import Value
from p4p.client.thread import Context


def test_ioc(softioc_subprocess: tuple[str, Queue]):
    pv_prefix, _ = softioc_subprocess
    ctxt = Context("pva")

    _parent_pvi = ctxt.get(f"{pv_prefix}:PVI")
    assert isinstance(_parent_pvi, Value)
    parent_pvi = _parent_pvi.todict()
    assert all(f in parent_pvi for f in ("alarm", "display", "timeStamp", "value"))
    assert parent_pvi["display"] == {"description": "The records in this controller"}
    assert parent_pvi["value"] == {
        "a": {"r": f"{pv_prefix}:A"},
        "b": {"r": f"{pv_prefix}:B_RBV", "w": f"{pv_prefix}:B"},
        "childvector": {"d": f"{pv_prefix}:ChildVector:PVI"},
    }

    child_vector_pvi_pv = parent_pvi["value"]["childvector"]["d"]
    _child_vector_pvi = ctxt.get(child_vector_pvi_pv)
    assert isinstance(_child_vector_pvi, Value)
    _child_vector_pvi = _child_vector_pvi.todict()
    assert all(
        f in _child_vector_pvi for f in ("alarm", "display", "timeStamp", "value")
    )
    assert _child_vector_pvi["display"] == {
        "description": "The records in this controller"
    }
    assert _child_vector_pvi["value"] == {
        "__0": {"d": f"{pv_prefix}:ChildVector:0:PVI"},
        "__1": {"d": f"{pv_prefix}:ChildVector:1:PVI"},
    }

    child_pvi_pv = _child_vector_pvi["value"]["__0"]["d"]
    _child_pvi = ctxt.get(child_pvi_pv)
    assert isinstance(_child_pvi, Value)
    child_pvi = _child_pvi.todict()
    assert all(f in child_pvi for f in ("alarm", "display", "timeStamp", "value"))
    assert child_pvi["display"] == {"description": "The records in this controller"}
    assert child_pvi["value"] == {
        "c": {"w": f"{pv_prefix}:ChildVector:0:C"},
        "d": {"x": f"{pv_prefix}:ChildVector:0:D"},
    }

    # Assert alias. Aliases do not show up in PVI structure
    assert ctxt.get(f"{pv_prefix}:B") == ctxt.get(f"{pv_prefix}:AliasB") == 0
    ctxt.put(f"{pv_prefix}:B", 10, wait=True)
    assert ctxt.get(f"{pv_prefix}:AliasB") == 10
    ctxt.put(f"{pv_prefix}:AliasB", 20, wait=True)
    assert ctxt.get(f"{pv_prefix}:B") == 20
    assert ctxt.get(f"{pv_prefix}:B_RBV") == ctxt.get(f"{pv_prefix}:AliasB_RBV") == 20
