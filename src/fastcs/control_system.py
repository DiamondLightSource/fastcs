import asyncio
import os
import signal
from collections.abc import Coroutine, Sequence
from functools import partial
from typing import Any

from IPython.terminal.embed import InteractiveShellEmbed

from fastcs.controllers import ControllerSpec
from fastcs.exceptions import FatalError
from fastcs.logging import logger
from fastcs.runner import ControllerRunner
from fastcs.tracer import Tracer
from fastcs.transports import Transport

tracer = Tracer()


class FastCS:
    """Entrypoint for a FastCS application.

    This class takes one or more `ControllerSpec` instances, runs them with a
    `ControllerRunner` and serves their APIs over the given `Transport` instances.

    Args:
        specs: What to construct each top-level controller from
        transports: A list of transports to serve the API over
        loop: Optional event loop to run the control system in
    """

    def __init__(
        self,
        specs: Sequence[ControllerSpec],
        transports: Sequence[Transport],
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self._specs: list[ControllerSpec] = list(specs)
        if not self._specs:
            raise ValueError("FastCS requires at least one controller")
        ids = [spec.id for spec in self._specs]
        if len(set(ids)) != len(ids):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(
                f"FastCS received controllers with duplicate ids {duplicates}"
            )
        self._ids = ids
        self._transports = transports
        self._loop = loop or asyncio.get_event_loop()

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
        # Build the APIs before wiring transports to them, so that a transport
        # registers its callbacks before the first values are read.
        self._runner = await ControllerRunner.build(self._specs)

        try:
            context = {
                "controllers": dict(
                    zip(self._ids, self._runner.controllers, strict=True)
                ),
                "controller_apis": dict(
                    zip(self._ids, self._runner.controller_apis, strict=True)
                ),
                "transports": [
                    transport.__class__.__name__ for transport in self._transports
                ],
            }

            for transport in self._transports:
                transport.connect(
                    controller_apis=self._runner.controller_apis, loop=self._loop
                )
                common_context = context.keys() & transport.context.keys()
                if common_context:
                    current = {k: context[k] for k in common_context}
                    new = {k: transport.context[k] for k in common_context}
                    raise RuntimeError(
                        "Duplicate context keys found between "
                        f"current context {current} "
                        f"and {transport.__class__.__name__} context: {new}"
                    )
                context.update(transport.context)

            logger.info(
                "Starting FastCS",
                controllers=self._ids,
                transports=f"[{', '.join(str(t) for t in self._transports)}]",
            )

            await self._runner.start()

            coros: list[Coroutine] = [t.serve() for t in self._transports]
            if interactive:
                coros.append(self._interactive_shell(context))
            else:

                async def block_forever():
                    while True:
                        await asyncio.sleep(1)

                coros.append(block_forever())

            coros.append(self._runner.wait_for_fatal_error())
            try:
                await asyncio.gather(*coros)
            except asyncio.CancelledError:
                pass
            except FatalError:
                logger.exception("Stopping FastCS after a fatal error")
                raise
            except Exception:
                logger.exception("Unhandled exception in serve")
        finally:
            logger.info("Shutting down FastCS")
            await self._runner.stop()

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
