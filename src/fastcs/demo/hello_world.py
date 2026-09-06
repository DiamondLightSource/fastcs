"""Example 1 - hello world: a controller made entirely of soft values.

The first rung of the ladder, and the only one with no device behind it. Every
value here lives in the controller object itself, so this module runs with no
simulator, no socket and no external process - which is the point: it shows the
``@attr`` spelling on its own, with nothing else to read past.

An attribute is the method that reads it::

    @attr
    async def greeting(self) -> str:
        \"\"\"The word to greet with.\"\"\"
        return self._greeting

That single decorated method is a read-only ``AttrR[str]``: the datatype is the
return annotation, and the docstring's first line becomes the description a
transport shows next to the value. Adding a ``@greeting.setter`` method makes
``greeting`` an ``AttrRW[str]``::

    @greeting.setter
    async def set_greeting(self, value: str) -> None:
        self._greeting = value

The setter has a name of its own, as PyTango's ``write_greeting`` does, so the
two halves of one attribute are never two declarations of one name.

The decorator's optional leading argument is a schedule, and its keyword
arguments are the attribute's metadata - so ``@attr(Polled(period=0.2),
units="s")`` is a value read every 0.2 seconds, served in seconds. Both are the
same vocabulary the procedural ``AttrR(float, getter=Polled(...), units="s")``
form uses; ``@attr`` is sugar over those constructors rather than a second way
of doing it.

Where to go next: ``fastcs.demo.temperature_attr`` wires the same attributes to a
real device by passing bound protocol methods to ``AttrRW(getter=...,
setter=...)``, which is what you want as soon as there is IO to do.
"""

import time

from fastcs.attributes import Polled, attr
from fastcs.controllers import Controller


class HelloWorldController(Controller):
    """A greeting, the message it makes, and how long it has been running.

    Nothing in here does any IO. ``message`` recomputes from the current
    ``greeting`` each time it is polled, so setting ``greeting`` visibly moves
    a second value - the same thing a real device does when one parameter
    depends on another, without needing a device to demonstrate it.
    """

    def __init__(self, subject: str = "world") -> None:
        super().__init__()

        self._greeting = "Hello"
        self._subject = subject
        self._started = time.monotonic()

    @attr
    async def greeting(self) -> str:
        """The word to greet with."""
        # A bare `@attr` is read once, when the controller connects, which is
        # what a value only changes because you changed it needs.
        return self._greeting

    @greeting.setter
    async def set_greeting(self, value: str) -> None:
        self._greeting = value

    @attr(Polled(period=0.2))
    async def message(self) -> str:
        """The greeting as it currently reads."""
        return f"{self._greeting}, {self._subject}!"

    @attr(Polled(period=0.2), units="s", precision=1)
    async def uptime(self) -> float:
        """Seconds since the controller was constructed."""
        return time.monotonic() - self._started
