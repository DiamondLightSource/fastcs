import asyncio
import os
import signal
from collections.abc import Coroutine, Sequence
from functools import partial
from typing import Any

from IPython.terminal.embed import InteractiveShellEmbed

from fastcs.controllers import Controller, ControllerAPI, ControllerRunner
from fastcs.logging import logger
from fastcs.tracer import Tracer
from fastcs.transports import Transport

tracer = Tracer()


def _context_key(controller: Controller) -> str:
    """Key used for a controller in IPython context dicts.

    Falls back to the class name when no path has been seeded so
    direct-construction callers (without launch()) still get a sensible key.
    """
    try:
        return controller.path[0]
    except IndexError:
        return controller.__class__.__name__


class FastCS:
    """Entrypoint for a FastCS application.

    This class takes one or more `Controller`s, creates asyncio tasks to run their
    update loops and builds their APIs to serve over the given `Transport`s.

    Args:
        controllers: The controller(s) to serve in the control system. Accepts
            either a single ``Controller`` or a sequence of them.
        transports: A list of transports to serve the API over
        loop: Optional event loop to run the control system in
    """

    def __init__(
        self,
        controllers: Controller | Sequence[Controller],
        transports: Sequence[Transport],
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        if isinstance(controllers, Controller):
            controllers = [controllers]
        self._controllers: list[Controller] = list(controllers)
        if not self._controllers:
            raise ValueError("FastCS requires at least one controller")
        keys = [_context_key(c) for c in self._controllers]
        if len(set(keys)) != len(keys):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            raise ValueError(
                f"FastCS received controllers with duplicate context keys "
                f"{duplicates}; ensure each controller has a unique id"
            )
        self._transports = transports
        self._loop = loop or asyncio.get_event_loop()

        self._runner = ControllerRunner(self._controllers, self._loop)
        self.controller_apis: list[ControllerAPI] = []

    def run(self, interactive: bool = True):
        """Run the application

        This is a convenience method to call `serve` in a synchronous context.

        Args:
            interactive: Whether to create an interactive IPython shell

        """
        serve = asyncio.ensure_future(self.serve(interactive=interactive))

        if os.name != "nt":
            self._loop.add_signal_handler(signal.SIGINT, serve.cancel)
            self._loop.add_signal_handler(signal.SIGTERM, serve.cancel)
        self._loop.run_until_complete(serve)

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
        try:
            coros = await self._start(interactive)
        except BaseException:
            # A failure during startup aborts: a partly built application is worse
            # than none, because clients connect successfully and never find what
            # they are looking for. Close whatever was opened on the way up, then
            # let the caller - or the orchestrator - see why.
            await self._runner.stop()
            raise

        try:
            await asyncio.gather(*coros)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unhandled exception in serve")
        finally:
            logger.info("Shutting down FastCS")
            await self._runner.stop()
            if self._runner.fatal_reason is not None:
                raise self._runner.fatal_reason

    async def _start(self, interactive: bool) -> list[Coroutine]:
        """Bring the application up, and return what ``serve`` should await."""
        # Build the APIs before wiring transports to them: a transport
        # registers its callbacks when it connects, and would miss the first
        # readback if the controllers had already started.
        self.controller_apis = await self._runner.build()

        context = {
            "controllers": {_context_key(c): c for c in self._controllers},
            "controller_apis": {
                _context_key(c): api
                for c, api in zip(self._controllers, self.controller_apis, strict=True)
            },
            "transports": [
                transport.__class__.__name__ for transport in self._transports
            ],
        }

        coros: list[Coroutine] = []
        for transport in self._transports:
            transport.connect(controller_apis=self.controller_apis, loop=self._loop)
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
            controllers=[_context_key(c) for c in self._controllers],
            transports=f"[{', '.join(str(t) for t in self._transports)}]",
        )

        await self._runner.start()

        # A fatal runner condition - a device coming back describing itself
        # differently, say - happens in a background task, where a raise would be
        # invisible. The runner records it instead, and this coroutine is where the
        # process notices and comes down rather than serving a tree that no longer
        # matches the hardware. Nothing calls ``sys.exit``, so an embedder sees an
        # exception out of ``serve`` rather than losing its process.
        async def fail_on_fatal() -> None:
            await self._runner.fatal_error.wait()
            assert self._runner.fatal_reason is not None
            raise self._runner.fatal_reason

        coros.append(fail_on_fatal())

        return coros

    async def _interactive_shell(self, context: dict[str, Any]):
        """Spawn interactive shell in another thread and wait for it to complete."""

        def run(coro: Coroutine[None, None, None]):
            """Run coroutine on FastCS event loop from IPython thread."""

            def wrapper():
                asyncio.create_task(coro)

            self._loop.call_soon_threadsafe(wrapper)

        async def interactive_shell(
            context: dict[str, object], stop_event: asyncio.Event
        ):
            """Run interactive shell in a new thread."""
            shell = InteractiveShellEmbed()
            await asyncio.to_thread(partial(shell.mainloop, local_ns=context))

            stop_event.set()

        context["run"] = run

        stop_event = asyncio.Event()
        self._loop.create_task(interactive_shell(context, stop_event))
        await stop_event.wait()

    def __del__(self):
        self._runner._cancel_tasks()  # noqa: SLF001
