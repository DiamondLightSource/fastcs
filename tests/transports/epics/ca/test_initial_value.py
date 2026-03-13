import asyncio
import enum

import numpy as np
import pytest

import fastcs.transports.epics.ca.ioc as ca_ioc
from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import Bool, Enum, Float, Int, String, Waveform
from fastcs.launch import FastCS
from fastcs.transports.epics import EpicsIOCOptions
from fastcs.transports.epics.ca.transport import EpicsCATransport


class InitialEnum(enum.Enum):
    A = 0
    B = 1
    C = 2


class InitialValuesController(Controller):
    int = AttrRW(Int(), initial_value=4)
    float = AttrRW(Float(), initial_value=3.1)
    bool = AttrRW(Bool(), initial_value=True)
    enum = AttrRW(Enum(InitialEnum), initial_value=InitialEnum.B)
    str = AttrRW(String(), initial_value="initial")
    waveform = AttrRW(
        Waveform(np.int64, shape=(10,)),
        initial_value=np.array(range(10), dtype=np.int64),
    )
    int_r = AttrR(Int(), initial_value=5)
    float_r = AttrR(Float(), initial_value=4.1)
    bool_r = AttrR(Bool(), initial_value=False)
    enum_r = AttrR(Enum(InitialEnum), initial_value=InitialEnum.C)
    str_r = AttrR(String(), initial_value="initial_r")
    waveform_r = AttrR(
        Waveform(np.int64, shape=(10,)),
        initial_value=np.array(range(10, 20), dtype=np.int64),
    )
    int_w = AttrW(Int())
    float_w = AttrW(Float())
    bool_w = AttrW(Bool())
    enum_w = AttrW(Enum(InitialEnum))
    str_w = AttrW(String())
    waveform_w = AttrW(Waveform(np.int64, shape=(10,)))


@pytest.mark.forked
@pytest.mark.asyncio
async def test_initial_values_set_in_ca(mocker):
    pv_prefix = "SOFTIOC_INITIAL_DEVICE"

    controller = InitialValuesController()
    fastcs = FastCS(
        controller,
        [EpicsCATransport(epicsca=EpicsIOCOptions(pv_prefix=pv_prefix))],
    )

    record_spy = mocker.spy(ca_ioc, "_make_in_record")
    record_spy_out = mocker.spy(ca_ioc, "_make_out_record")

    task = asyncio.create_task(fastcs.serve(interactive=False))
    try:
        async with asyncio.timeout(3):
            while not record_spy.spy_return_list or not record_spy_out.spy_return_list:
                await asyncio.sleep(0)

        initial_values = {
            wrapper.name: wrapper.get()
            for wrapper in record_spy.spy_return_list + record_spy_out.spy_return_list
        }
        for name, value in {
            "SOFTIOC_INITIAL_DEVICE:Bool": 1,
            "SOFTIOC_INITIAL_DEVICE:BoolR": 0,
            "SOFTIOC_INITIAL_DEVICE:BoolW": 0,
            "SOFTIOC_INITIAL_DEVICE:Bool_RBV": 1,
            "SOFTIOC_INITIAL_DEVICE:Enum": 1,
            "SOFTIOC_INITIAL_DEVICE:EnumR": 2,
            "SOFTIOC_INITIAL_DEVICE:EnumW": 0,
            "SOFTIOC_INITIAL_DEVICE:Enum_RBV": 1,
            "SOFTIOC_INITIAL_DEVICE:Float": 3.1,
            "SOFTIOC_INITIAL_DEVICE:FloatR": 4.1,
            "SOFTIOC_INITIAL_DEVICE:FloatW": 0.0,
            "SOFTIOC_INITIAL_DEVICE:Float_RBV": 3.1,
            "SOFTIOC_INITIAL_DEVICE:Int": 4,
            "SOFTIOC_INITIAL_DEVICE:IntR": 5,
            "SOFTIOC_INITIAL_DEVICE:IntW": 0,
            "SOFTIOC_INITIAL_DEVICE:Int_RBV": 4,
            "SOFTIOC_INITIAL_DEVICE:Str": "initial",
            "SOFTIOC_INITIAL_DEVICE:StrR": "initial_r",
            "SOFTIOC_INITIAL_DEVICE:StrW": "",
            "SOFTIOC_INITIAL_DEVICE:Str_RBV": "initial",
            "SOFTIOC_INITIAL_DEVICE:Waveform": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            "SOFTIOC_INITIAL_DEVICE:WaveformR": [
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                18,
                19,
            ],
            "SOFTIOC_INITIAL_DEVICE:WaveformW": 10 * [0],
            "SOFTIOC_INITIAL_DEVICE:Waveform_RBV": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        }.items():
            assert np.array_equal(value, initial_values[name])
    except Exception as e:
        raise e
    finally:
        task.cancel()
