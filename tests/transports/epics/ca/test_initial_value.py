import asyncio
import enum

import numpy as np
import pytest

import fastcs.transports.epics.ca.ioc as ca_ioc
from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import Array1D
from fastcs.launch import FastCS
from fastcs.transports.epics.ca.transport import EpicsCATransport


class InitialEnum(enum.Enum):
    A = 0
    B = 1
    C = 2


class InitialValuesController(Controller):
    int_rw = AttrRW(int, initial_value=4)
    float_rw = AttrRW(float, initial_value=3.1)
    bool_rw = AttrRW(bool, initial_value=True)
    enum_rw = AttrRW(InitialEnum, initial_value=InitialEnum.B)
    str_rw = AttrRW(str, initial_value="initial")
    waveform_rw = AttrRW(
        Array1D[np.int64],
        initial_value=np.array(range(10), dtype=np.int64),
        shape=(10,),
    )
    int_r = AttrR(int, initial_value=5)
    float_r = AttrR(float, initial_value=4.1)
    bool_r = AttrR(bool, initial_value=False)
    enum_r = AttrR(InitialEnum, initial_value=InitialEnum.C)
    str_r = AttrR(str, initial_value="initial_r")
    waveform_r = AttrR(
        Array1D[np.int64],
        initial_value=np.array(range(10, 20), dtype=np.int64),
        shape=(10,),
    )
    int_w = AttrW(int)
    float_w = AttrW(float)
    bool_w = AttrW(bool)
    enum_w = AttrW(InitialEnum)
    str_w = AttrW(str)
    waveform_w = AttrW(Array1D[np.int64], shape=(10,))


@pytest.mark.forked
@pytest.mark.asyncio
async def test_initial_values_set_in_ca(mocker):
    pv_prefix = "SOFTIOC_INITIAL_DEVICE"

    loop = asyncio.get_event_loop()
    controller = InitialValuesController()
    controller.set_path([pv_prefix])
    fastcs = FastCS(
        controller,
        [EpicsCATransport()],
        loop,
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
            "SOFTIOC_INITIAL_DEVICE:BoolRw": 1,
            "SOFTIOC_INITIAL_DEVICE:BoolR": 0,
            "SOFTIOC_INITIAL_DEVICE:BoolW": 0,
            "SOFTIOC_INITIAL_DEVICE:BoolRw_RBV": 1,
            "SOFTIOC_INITIAL_DEVICE:EnumRw": 1,
            "SOFTIOC_INITIAL_DEVICE:EnumR": 2,
            "SOFTIOC_INITIAL_DEVICE:EnumW": 0,
            "SOFTIOC_INITIAL_DEVICE:EnumRw_RBV": 1,
            "SOFTIOC_INITIAL_DEVICE:FloatRw": 3.1,
            "SOFTIOC_INITIAL_DEVICE:FloatR": 4.1,
            "SOFTIOC_INITIAL_DEVICE:FloatW": 0.0,
            "SOFTIOC_INITIAL_DEVICE:FloatRw_RBV": 3.1,
            "SOFTIOC_INITIAL_DEVICE:IntRw": 4,
            "SOFTIOC_INITIAL_DEVICE:IntR": 5,
            "SOFTIOC_INITIAL_DEVICE:IntW": 0,
            "SOFTIOC_INITIAL_DEVICE:IntRw_RBV": 4,
            "SOFTIOC_INITIAL_DEVICE:StrRw": "initial",
            "SOFTIOC_INITIAL_DEVICE:StrR": "initial_r",
            "SOFTIOC_INITIAL_DEVICE:StrW": "",
            "SOFTIOC_INITIAL_DEVICE:StrRw_RBV": "initial",
            "SOFTIOC_INITIAL_DEVICE:WaveformRw": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
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
            "SOFTIOC_INITIAL_DEVICE:WaveformRw_RBV": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        }.items():
            assert np.array_equal(value, initial_values[name])
    except Exception as e:
        raise e
    finally:
        task.cancel()
