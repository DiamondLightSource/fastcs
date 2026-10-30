import asyncio

from fastcs import FastCS
from fastcs.attributes import AttrR, AttrW
from fastcs.controllers import Controller
from fastcs.transports.epics.ca.transport import EpicsCATransport
from fastcs.transports.rest.options import RestServerOptions
from fastcs.transports.rest.transport import RestTransport
from fastcs.transports.tango.transport import TangoTransport


class MyTestController(Controller):
    read_int: AttrR = AttrR(int, initial_value=0)
    write_bool: AttrW = AttrW(bool)


def run():
    transport_options = [
        RestTransport(rest=RestServerOptions(port=8090)),
        EpicsCATransport(),
        TangoTransport(),
    ]
    controller = MyTestController()
    controller.set_path(["BENCHMARK-DEVICE"])
    instance = FastCS(controller, transport_options, asyncio.get_event_loop())
    instance.run()


if __name__ == "__main__":
    run()
