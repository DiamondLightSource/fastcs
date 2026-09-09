"""Example 4's protocol layer - a declarative vocabulary built on the filler.

A SCPI device does not describe itself. There is no parameter tree to walk and no
metadata to read off the wire, so the attributes and everything about them are
written by hand, in the class body, as annotated hints::

    class Thermostat(SCPIController):
        ramp_rate: Annotated[AttrRW[float], SCPIParam("R", precision=3, units="K/s")]

That is the declarative half of ADR 0013 for a device that cannot be introspected:
the class body says what exists and `SCPIController` provisions it, rather than
``__init__`` wiring each getter and setter by hand as
:mod:`fastcs.demo.temperature_attr` does.

**This lives in the demo, not core FastCS** (ADR 0014 decision 3). Core defines no
extras vocabulary for 1.0 - it defines the *mechanism*, which is the ``extras`` an
``Annotated`` hint carries through to `ControllerFiller`. What a protocol package
puts in there is its own business, and this module is the worked example of one
doing it: `SCPIParam` is a sibling of ophyd-async's ``PvSuffix``, not a FastCS type.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Unpack, cast

from fastcs.attributes import AttrW, Polled
from fastcs.connections import IPConnection
from fastcs.controllers import Controller
from fastcs.controllers.filler import Declaration
from fastcs.datatypes import DType_T, Meta
from fastcs.logging import logger


class SCPIParam:
    """One attribute's whole spec: its command token, plus its metadata.

    The command token and the metadata are written in the same place because they
    describe the same thing, and splitting them across two extras would let one be
    updated without the other::

        target: Annotated[AttrR[float], SCPIParam("T", precision=3, units="degC")]

    It is the **exclusive** spec source for its attribute: `SCPIController` does not
    also merge a separate ``FloatMeta`` extra on the same hint, so there is one
    place to look for what an attribute is.

    Named ``SCPIParam`` rather than ``SCPIMeta``: the ``*Meta`` suffix is reserved
    for the ``Unpack``-able typed dicts, and this is a binding extra you
    instantiate.

    Args:
        param: The device's command token - ``"R"`` for a device answering
            ``R?`` and accepting ``R=1.5``
        meta: Metadata for the attribute, validated against the datatype the hint
            declared when `ControllerFiller.fill_attribute` applies it

    """

    def __init__(self, param: str, **meta: Unpack[Meta]) -> None:
        self.param = param
        self.meta: Meta = meta

    def __repr__(self) -> str:
        return f"SCPIParam({self.param!r}, {self.meta})"


class SCPIController(Controller):
    """A controller whose attributes are declared as `SCPIParam` hints.

    Provisions each declared attribute's getter and setter from its command token,
    and applies the metadata that came with it. The wire format is the text protocol
    the demo's temperature sim speaks - ``R?`` to read, ``R=1.5`` to write - which is
    SCPI-shaped, so no new simulation is needed to demonstrate this.

    A sub controller addressing one channel of a device passes a ``suffix``, which is
    appended to every token: the same class serves ramp 1 as ``S01?`` and ramp 2 as
    ``S02?`` without dispatching on which at IO time.

    Args:
        connection: The link to talk over. Held as ``self.connection`` too, so
            scans are gated on it.
        suffix: Appended to every command token, for a per-channel sub controller
        description: Passed to `Controller`

    """

    poll_period: float = 0.2
    """Seconds between reads of every polled attribute this controller declares."""

    def __init__(
        self,
        connection: IPConnection,
        suffix: str = "",
        description: str | None = None,
    ) -> None:
        self._connection = connection
        self._suffix = suffix

        super().__init__(description)

        self.connection = connection

    async def initialise(self) -> None:
        """Provision every attribute the class body declared with a `SCPIParam`.

        Nothing here talks to the device: a SCPI device has nothing to ask. The
        work is turning static declarations into IO, which is why this reads only
        the class body and the connection it was given.
        """
        for declaration in self.filler.declarations.values():
            param = self._param_of(declaration)
            if param is None:
                # Not ours - a hint some other mechanism fills, or a promise the
                # driver provisions itself.
                continue

            self._fill(declaration, param)

        self.filler.check_filled()

    @staticmethod
    def _param_of(declaration: Declaration) -> SCPIParam | None:
        """The `SCPIParam` an ``Annotated`` hint carried, if it carried one.

        ``declaration.hint.extras`` is the ``(child, extras)`` the filler yields,
        looked up by name so there is something to call ``fill_attribute`` with.
        """
        for extra in declaration.hint.extras:
            if isinstance(extra, SCPIParam):
                return extra
        return None

    def _fill(self, declaration: Declaration, param: SCPIParam) -> None:
        datatype = declaration.hint.datatype
        if datatype is None:
            # ``state: AttrR`` is a promise for introspection to satisfy, and a
            # SCPI device introspects nothing - so there is no datatype to parse
            # the device's answer into and nothing this layer can build.
            raise TypeError(
                f"{type(self).__name__}.{declaration.raw_name} declares a "
                f"{param!r} but does not say what it holds. A SCPI device cannot "
                "be asked, so the hint must name its datatype - "
                f"`{declaration.raw_name}: Annotated[AttrRW[float], ...]`."
            )

        setter = None
        if isinstance(declaration.child, AttrW):
            setter = self._setter(param.param)

        self.filler.fill_attribute(
            declaration.name,
            # The hint's datatype doubles as the parser for the device's text
            # answer, which every datatype FastCS serves happens to support -
            # ``float("1.5")``, ``OnOffEnum("1")`` - but nothing in its type says
            # so, hence the cast.
            getter=Polled(
                self._getter(param.param, cast(Callable[[str], Any], datatype)),
                period=self.poll_period,
            ),
            setter=setter,
            **param.meta,
        )

    def _getter(
        self, param: str, datatype: Callable[[str], DType_T]
    ) -> Callable[[], Awaitable[DType_T]]:
        """A zero-argument coroutine reading one parameter, parsed into its type."""

        async def get() -> DType_T:
            query = f"{param}{self._suffix}?\r\n"
            response = (await self._connection.send_query(query)).strip("\r\n")
            logger.trace("Query for attribute", query=query, response=response)
            return datatype(response)

        return get

    def _setter(self, param: str) -> Callable[[Any], Awaitable[None]]:
        """A one-argument coroutine writing one parameter."""

        async def put(value: Any) -> None:
            command = f"{param}{self._suffix}={value}\r\n"
            await self._connection.send_command(command)
            logger.trace("Send command for attribute", command=command)

        return put
