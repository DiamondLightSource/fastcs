import asyncio
import os
import signal
from collections.abc import Coroutine, Sequence
from functools import partial
from typing import Any

from IPython.terminal.embed import InteractiveShellEmbed

from fastcs.controllers import Controller
from fastcs.logging import logger
from fastcs.methods import ScanCallback
from fastcs.tracer import Tracer
from fastcs.transports import Transport

tracer = Tracer()


class FastCS:
    """Entrypoint for a FastCS application.

    This class takes a `Controller`, creates asyncio tasks to run its update loops and
    builds its API to serve over the given `Transport`s.

    Args:
        controller: The controller to serve in the control system
        transports: A list of transports to serve the API over
        loop: Optional event loop to run the control system in
    """

    def __init__(self, controller: Controller, transports: Sequence[Transport]):
        self._controller = controller
        self._transports = transports

        self._scan_coros: list[ScanCallback] = []
        self._initial_coros: list[ScanCallback] = []

        self._scan_tasks: set[asyncio.Task] = set()

    def run(self, interactive: bool = True):
        """Run the application

        This is a convenience method to call `serve` in a synchronous context.
        To use in an async context, call `serve` directly.

        Args:
            interactive: Whether to create an interactive IPython shell

        """

        async def _serve():
            """Wrapper to add signal handlers and call `serve`"""
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()

            if os.name != "nt" and task is not None:
                loop.add_signal_handler(signal.SIGINT, task.cancel)
                loop.add_signal_handler(signal.SIGTERM, task.cancel)

            await self.serve(interactive=interactive)

        asyncio.run(_serve())

    async def serve(self, interactive: bool = True) -> None:
        """Serve the control system over the given transports on the current event loop

        This is the main entrypoint for a FastCS application. It can be awaited
        directly, which will block until stopped, or to allow further logic or
        interaction with the system it can be run as a background task. If doing the
        latter, generally it should be called with ``interactive=False`` for more
        reliable stdout capture.

        To call from a synchronous context, use the `run` method.

        Args:
            interactive: Whether to create an interactive IPython shell

        """
        await self._controller.initialise()
        self._controller.post_initialise()

        self.controller_api, self._scan_coros, self._initial_coros = (
            self._controller.create_api_and_tasks()
        )

        context = {
            "controller": self._controller,
            "controller_api": self.controller_api,
            "transports": [
                transport.__class__.__name__ for transport in self._transports
            ],
        }

        coros: list[Coroutine] = []
        for transport in self._transports:
            transport.connect(controller_api=self.controller_api)
            coros.append(transport.serve())
            common_context = context.keys() & transport.context.keys()
            if common_context:
                raise RuntimeError(
                    "Duplicate context keys found between "
                    f"current context { ({k: context[k] for k in common_context}) } "
                    f"and {transport.__class__.__name__} context: "
                    f"{ ({k: transport.context[k] for k in common_context}) }"
                )
            context.update(transport.context)

        if interactive:
            coros.append(self._interactive_shell(context))
        else:

            async def block_forever():
                while True:
                    await asyncio.sleep(1)

            coros.append(block_forever())

        logger.info(
            "Starting FastCS",
            controller=self._controller,
            transports=f"[{', '.join(str(t) for t in self._transports)}]",
        )

        await self._controller.connect()
        await self._run_initial_coros()
        await self._start_scan_tasks()

        try:
            await asyncio.gather(*coros)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unhandled exception in serve")
        finally:
            logger.info("Shutting down FastCS")
            self._stop_scan_tasks()
            await self._controller.disconnect()

    async def _run_initial_coros(self):
        for coro in self._initial_coros:
            await coro()

    async def _start_scan_tasks(self):
        self._scan_tasks = {asyncio.create_task(coro()) for coro in self._scan_coros}

    async def _interactive_shell(self, context: dict[str, Any]):
        """Spawn interactive shell in another thread and wait for it to complete."""
        loop = asyncio.get_running_loop()

        def run(coro: Coroutine[None, None, None]):
            """Run coroutine on FastCS event loop from IPython thread."""

            def wrapper():
                task = asyncio.create_task(coro)

                def _log_exception(t: asyncio.Task):
                    if not t.cancelled() and (exc := t.exception()):
                        logger.exception("`run` task raised exception", exc_info=exc)

                task.add_done_callback(_log_exception)

            loop.call_soon_threadsafe(wrapper)

        async def interactive_shell(
            context: dict[str, object], stop_event: asyncio.Event
        ):
            """Run interactive shell in a new thread."""
            shell = InteractiveShellEmbed()
            await asyncio.to_thread(partial(shell.mainloop, local_ns=context))

            stop_event.set()

        context["run"] = run

        stop_event = asyncio.Event()
        shell_task = asyncio.create_task(interactive_shell(context, stop_event))

        await stop_event.wait()

        if not shell_task.cancelled() and (exc := shell_task.exception()):
            logger.exception("Interactive shell raised exception", exc_info=exc)

    def _stop_scan_tasks(self):
        for task in self._scan_tasks:
            if not task.done():
                try:
                    task.cancel()
                except (asyncio.CancelledError, RuntimeError):
                    pass
                except Exception as e:
                    raise RuntimeError("Unhandled exception in stop scan tasks") from e

        self._scan_tasks.clear()

    def __del__(self):
        self._stop_scan_tasks()
