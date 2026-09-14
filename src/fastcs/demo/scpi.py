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


Every declared attribute reads and writes through the same wire protocol, differing
only in its command token and (for a getter) the datatype that parses the device's
text answer - the shared `AttrBackend`/`AttrFactory` mechanism this module's
`SCPIBackend` is a worked example of, rather than a getter/setter closure hand-built
per attribute.
"""

from collections.abc import Callable
from typing import Any, Unpack, cast

from fastcs.attributes import Polled
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


class SCPIBackend:
    """Reads and writes one SCPI parameter, given its mnemonic and datatype.

    Shared across every attribute an `SCPIController` declares, rather than a
    getter/setter closure hand-built per attribute: `SCPIController._fill` supplies
    the mnemonic and the datatype that parses the device's text answer as call
    arguments (see `fastcs.attributes.factory.AttrFactory`), not as construction
    state closed over per attribute.
    """

    def __init__(self, connection: IPConnection, suffix: str) -> None:
        self._connection = connection
        self._suffix = suffix

    async def get(self, param: str, datatype: Callable[[str], DType_T]) -> DType_T:
        query = f"{param}{self._suffix}?\r\n"
        response = (await self._connection.send_query(query)).strip("\r\n")
        # The hint's datatype doubles as the parser for the device's text answer,
        # which every datatype FastCS serves happens to support - ``float("1.5")``,
        # ``OnOffEnum("1")`` - but nothing in its type says so, hence the cast at
        # the call site that built this `datatype` argument.
        return datatype(response)

    async def set(
        self, value: DType_T, param: str, _datatype: Callable[[str], DType_T]
    ) -> None:
        command = f"{param}{self._suffix}={value}\r\n"
        await self._connection.send_command(command)
        logger.trace("Send command for attribute", command=command)


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
        self._backend = SCPIBackend(connection, suffix)

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

        self.filler.fill_from_backend(
            declaration,
            self._backend,
            param.param,
            cast(Callable[[str], Any], datatype),
            schedule=Polled(period=self.poll_period),
            **param.meta,
        )
