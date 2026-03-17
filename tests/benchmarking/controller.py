from fastcs import FastCS
from fastcs.attributes import AttrR, AttrW
from fastcs.controllers import Controller
from fastcs.datatypes import Bool, Int
from fastcs.transports.epics import EpicsIOCOptions
from fastcs.transports.epics.ca.transport import EpicsCATransport
from fastcs.transports.rest.options import RestServerOptions
from fastcs.transports.rest.transport import RestTransport
from fastcs.transports.tango.options import TangoDSROptions
from fastcs.transports.tango.transport import TangoTransport


class MyTestController(Controller):
    read_int: AttrR = AttrR(Int(), initial_value=0)
    write_bool: AttrW = AttrW(Bool())


def run():
    transport_options = [
        RestTransport(rest=RestServerOptions(port=8090)),
        EpicsCATransport(
            epicsca=EpicsIOCOptions(pv_prefix="BENCHMARK-DEVICE"),
        ),
        TangoTransport(tango=TangoDSROptions(dev_name="MY/BENCHMARK/DEVICE")),
    ]
    instance = FastCS(MyTestController(), transport_options)
    instance.run()


if __name__ == "__main__":
    run()
