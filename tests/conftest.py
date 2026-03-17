import asyncio
import io
import multiprocessing
import os
import random
import signal
import string
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from multiprocessing.context import DefaultContext
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from aioca import purge_channel_caches
from softioc import builder

from fastcs.attributes import AttrR, AttrRW, AttrW
from fastcs.datatypes import Bool, Float, Int, String
from fastcs.logging import configure_logging, logger
from fastcs.logging._logging import LogLevel
from fastcs.transports.tango.dsr import register_dev
from tests.assertable_controller import MyTestAttributeIORef, MyTestController
from tests.example_p4p_ioc import run as _run_p4p_ioc
from tests.example_softioc import run as _run_softioc


@pytest.fixture
def loguru_caplog(caplog):
    handler_id = logger.add(caplog.handler, format="{message}", level="TRACE")
    yield caplog
    logger.remove(handler_id)


@pytest.fixture(scope="function", autouse=True)
def clear_softioc_records():
    builder.ClearRecords()


class BackendTestController(MyTestController):
    read_int: AttrR = AttrR(Int(), io_ref=MyTestAttributeIORef())
    read_write_int: AttrRW = AttrRW(Int(), io_ref=MyTestAttributeIORef())
    read_write_float: AttrRW = AttrRW(Float())
    read_bool: AttrR = AttrR(Bool())
    write_bool: AttrW = AttrW(Bool(), io_ref=MyTestAttributeIORef())
    read_string: AttrRW = AttrRW(String())


@pytest.fixture
def controller():
    return BackendTestController()


@pytest.fixture
def controller_api(controller):
    return controller._build_api([])


DATA_PATH = Path(__file__).parent / "data"


@pytest.fixture
def data() -> Path:
    return DATA_PATH


# Prevent pytest from catching exceptions when debugging in vscode so that break on
# exception works correctly (see: https://github.com/pytest-dev/pytest/issues/7409)
if os.getenv("PYTEST_RAISE", "0") == "1":

    @pytest.hookimpl(tryfirst=True)
    def pytest_exception_interact(call: pytest.CallInfo[Any]):
        if call.excinfo is not None:
            raise call.excinfo.value
        else:
            raise RuntimeError(
                f"{call} has no exception data, an unknown error has occurred"
            )

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(excinfo: pytest.ExceptionInfo[Any]):
        raise excinfo.value


PV_PREFIX = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
HERE = Path(os.path.dirname(os.path.abspath(__file__)))


def _run_ioc_as_subprocess(
    pv_prefix: str,
    run_ioc: Callable,
    error_queue: multiprocessing.Queue,
    stdout_queue: multiprocessing.Queue,
):
    configure_logging(LogLevel.TRACE)
    logger.add(print)  # forward log messages to stdout for start up detection in tests

    try:
        from pytest_cov.embed import cleanup_on_sigterm
    except ImportError:
        ...
    else:
        cleanup_on_sigterm()

    class QueueWriter(io.TextIOBase):
        def __init__(self, queue):
            self.queue = queue

        def write(self, s):  # type: ignore
            self.queue.put(s)

    try:
        sys.stdout = QueueWriter(stdout_queue)
        run_ioc(pv_prefix=pv_prefix)

    except Exception as e:
        error_queue.put(e)


def run_ioc_as_subprocess(
    run_ioc: Callable, ctxt: DefaultContext
) -> Generator[tuple[str, multiprocessing.Queue], None, None]:
    ioc_startup_timeout = 10
    ioc_startup_timeout_error = TimeoutError("IOC did not start in time")

    pv_prefix = str(uuid4())
    error_queue = ctxt.Queue()
    stdout_queue = ctxt.Queue()
    process = ctxt.Process(
        target=_run_ioc_as_subprocess,
        args=(pv_prefix, run_ioc, error_queue, stdout_queue),
    )
    process.start()

    try:
        start_time = time.monotonic()
        while True:
            try:
                if "Running IOC" in (
                    stdout_queue.get(timeout=ioc_startup_timeout)  # type: ignore
                ):
                    stdout_queue.get()  # get the newline
                    break
            except Exception as error:
                raise ioc_startup_timeout_error from error
            if time.monotonic() - start_time > ioc_startup_timeout:
                raise ioc_startup_timeout_error

        time.sleep(0.5)
        yield pv_prefix, stdout_queue

    finally:
        # Propogate errors
        if not error_queue.empty():
            raise error_queue.get()

        # close ca caches before the event loop
        purge_channel_caches()

        error_queue.close()
        stdout_queue.close()
        process.terminate()
        process.join(timeout=ioc_startup_timeout)


@pytest.fixture(scope="module")
def p4p_subprocess():
    multiprocessing.set_start_method("forkserver", force=True)
    yield from run_ioc_as_subprocess(_run_p4p_ioc, multiprocessing.get_context())


@pytest.fixture(scope="module")
def softioc_subprocess():
    multiprocessing.set_start_method("spawn", force=True)
    yield from run_ioc_as_subprocess(_run_softioc, multiprocessing.get_context())


@pytest.fixture(scope="session")
def tango_system():
    subprocess.run(
        ["podman", "compose", "-f", HERE / "benchmarking" / "compose.yaml", "up", "-d"],
        check=True,
    )
    yield
    subprocess.run(
        ["podman", "compose", "-f", HERE / "benchmarking" / "compose.yaml", "down"],
        check=True,
    )


@pytest.fixture(scope="session")
def register_device():
    attempts = 10
    sleep = 1

    if not os.getenv("TANGO_HOST"):
        raise RuntimeError("TANGO_HOST not defined")

    for attempt in range(1, attempts + 1):
        try:
            register_dev(
                dev_name="MY/BENCHMARK/DEVICE",
                dev_class="TestController",
                dsr_instance="MY_SERVER_INSTANCE",
            )
            break
        except Exception:
            time.sleep(sleep)
        if attempt == attempts:
            raise TimeoutError("Tango device could not be registered")


@pytest.fixture(scope="session")
def test_controller(tango_system, register_device):
    timeout = 10
    process = subprocess.Popen(
        ["python", HERE / "benchmarking" / "controller.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )

    start_time = time.monotonic()
    while "Uvicorn running" not in (
        process.stdout.readline().strip()  # type: ignore
    ):
        if time.monotonic() - start_time > timeout:
            raise TimeoutError("Controller did not start in time")

    # close ca caches before the event loop
    purge_channel_caches()

    # Stop buffer from getting full and blocking the subprocess
    for f in [process.stdin, process.stdout, process.stderr]:
        if f:
            f.close()

    yield process

    process.send_signal(signal.SIGINT)
    process.wait(timeout)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
