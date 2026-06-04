"""Per-controller GUI / docs file emission for the EPICS transports.

D4 of #351: each transport produces ``output_dir/{id}{ext}`` for every
configured controller plus an ``index{ext}`` file linking to them.  The
index is always emitted -- even for a single controller -- so the file
layout is stable as the number of controllers changes.  Controllers
appear in the index in the order they were declared in ``fastcs.yaml``,
which is the order in which ``controller_apis`` is passed in.
"""

from collections.abc import Callable

from pvi._format.dls import DLSFormatter  # type: ignore
from pvi.device import (  # type: ignore
    NON_PASCAL_CHARS_RE,
    Device,
    DeviceRef,
    enforce_pascal_case,
)

from fastcs.controllers import ControllerAPI
from fastcs.logging import logger
from fastcs.transports.epics.gui import EpicsGUI
from fastcs.transports.epics.options import (
    EpicsDocsOptions,
    EpicsGUIFormat,
    EpicsGUIOptions,
)

GUIBuilder = Callable[[ControllerAPI], EpicsGUI]
"""Per-flavour ``EpicsGUI`` constructor (CA bare PV / PVA ``pva://`` PV)."""

INDEX_STEM = "index"


def _coerce_pascal_name(name: str) -> str:
    """Coerce an arbitrary controller id into a valid pvi ``PascalStr``.

    pvi's :py:class:`DeviceRef` constrains ``name`` to ``PascalStr`` (regex
    ``^([A-Z][a-z0-9]*)*$``). FastCS controller ids range over the looser
    union of every transport's charset and may legitimately start with a
    digit (e.g. UUID-flavoured test prefixes), so we prepend ``X`` when
    needed before delegating to ``pvi.device.enforce_pascal_case``.

    Some otherwise-valid transport ids (the EPICS CA validator accepts
    ``[A-Za-z0-9_-]+`` so ``"___"`` and ``"-"`` are legal) contain no
    Pascal-usable characters at all. ``enforce_pascal_case`` strips
    everything and then unconditionally indexes ``s[0]``, raising
    ``IndexError`` on the empty string. We fail fast with a clearer
    ``ValueError`` instead -- a silent fallback would produce nonsense
    GUI names that the user can't trace back to the offending id.
    """
    stripped = NON_PASCAL_CHARS_RE.sub("", name)
    if not stripped:
        raise ValueError(
            f"Controller id {name!r} contains no characters usable "
            "in a Pascal-case name; pick an id with at least one ASCII "
            "letter or digit"
        )
    candidate = enforce_pascal_case(name)
    if not candidate[0].isupper():
        candidate = "X" + candidate
    return candidate


def _collect_top_level_apis(
    controller_apis: list[ControllerAPI],
) -> list[ControllerAPI]:
    """Return every API that should get its own top-level screen file.

    Includes every root controller and any sub-controller whose path has
    exactly one segment (i.e. it carries a root-level PV prefix).
    Traversal order is depth-first; a ``seen`` set prevents duplicates.
    """
    result: list[ControllerAPI] = []
    seen: set[tuple[str, ...]] = set()

    def _walk(api: ControllerAPI, is_root: bool) -> None:
        key = tuple(api.path)
        if key in seen:
            return
        if is_root or len(api.path) == 1:
            seen.add(key)
            result.append(api)
        for sub_api in api.sub_apis.values():
            _walk(sub_api, False)

    for api in controller_apis:
        _walk(api, True)
    return result


def emit_gui_files(
    controller_apis: list[ControllerAPI],
    options: EpicsGUIOptions,
    gui_builder: GUIBuilder,
) -> None:
    """Write ``{id}{ext}`` per controller plus an index file in ``output_dir``.

    ``gui_builder`` lets each EPICS transport pick its PV-flavoured GUI
    builder (CA's ``EpicsGUI`` writes bare PVs; PVA's ``PvaEpicsGUI``
    prefixes them with ``pva://``).  An index file with one ``DeviceRef``
    button per controller sits at the root of ``output_dir`` regardless
    of how many controllers there are.  In addition to the root controllers,
    any sub-controller whose path has exactly one segment (i.e. a root-level
    PV prefix) is also emitted as a top-level screen with its own index entry.
    """
    if options.file_format is EpicsGUIFormat.edl:
        logger.warning("FastCS may not support all widgets in .edl screens")

    ext = options.file_format.value
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    formatter = DLSFormatter()

    refs: list[DeviceRef] = []
    for api in _collect_top_level_apis(controller_apis):
        name = api.path[0]
        ui_filename = f"{name}{ext}"
        controller_path = (output_dir / ui_filename).resolve()

        device = gui_builder(api).build_device(
            options.title
            if options.title is not None
            else (api.path[0] if api.path else "FastCS Devices")
        )
        formatter.format(device, controller_path)

        refs.append(
            DeviceRef(
                name=_coerce_pascal_name(name),
                label=name,
                pv=name,
                ui=ui_filename,
                macros={},
            )
        )

    index_path = (output_dir / f"{INDEX_STEM}{ext}").resolve()
    index_title = (
        options.title
        if options.title is not None
        else (
            controller_apis[0].path[0]
            if controller_apis and controller_apis[0].path
            else "FastCS Devices"
        )
    )
    formatter.format(Device(label=index_title, children=refs), index_path)


DOCS_EXT = ".md"


def emit_docs_files(
    controller_apis: list[ControllerAPI],
    options: EpicsDocsOptions,
) -> None:
    """Write ``{id}.md`` per controller plus ``index.md`` in ``output_dir``.

    Each per-controller file lists the controller's attributes and
    commands as a flat reference page.  The index links to each one in
    declaration order, mirroring the GUI emission.
    """
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    for api in controller_apis:
        name = api.path[0]
        path = output_dir / f"{name}{DOCS_EXT}"
        path.write_text(_render_controller_md(api, options.depth))

    index_path = output_dir / f"{INDEX_STEM}{DOCS_EXT}"
    index_path.write_text(
        _render_index_md(
            controller_apis,
            options.title
            if options.title is not None
            else (
                controller_apis[0].path[0]
                if controller_apis and controller_apis[0].path
                else "FastCS Devices"
            ),
        )
    )


def _render_index_md(controller_apis: list[ControllerAPI], title: str) -> str:
    lines = [f"# {title}", ""]
    for api in controller_apis:
        name = api.path[0]
        lines.append(f"- [{name}]({name}{DOCS_EXT})")
    lines.append("")
    return "\n".join(lines)


def _render_controller_md(
    api: ControllerAPI, depth: int | None, _level: int = 0
) -> str:
    if depth is not None and _level > depth:
        return ""

    heading = "#" * (_level + 1)
    title = ":".join(api.path) if api.path else "(root)"
    lines = [f"{heading} {title}", ""]

    if api.attributes:
        lines.append(f"{heading}# Attributes")
        lines.append("")
        for name in api.attributes:
            lines.append(f"- `{name}`")
        lines.append("")

    if api.command_methods:
        lines.append(f"{heading}# Commands")
        lines.append("")
        for name in api.command_methods:
            lines.append(f"- `{name}`")
        lines.append("")

    for sub_api in api.sub_apis.values():
        lines.append(_render_controller_md(sub_api, depth, _level + 1))

    return "\n".join(lines).rstrip() + "\n"
