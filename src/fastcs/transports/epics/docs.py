"""Re-exports kept for backwards-compatible imports.

Per-controller docs file emission lives in
:py:mod:`fastcs.transports.epics.emission`; transports call
:py:func:`fastcs.transports.epics.emission.emit_docs_files` directly with
the full ``list[ControllerAPI]``.
"""

from fastcs.transports.epics.emission import emit_docs_files
from fastcs.transports.epics.options import EpicsDocsOptions

__all__ = ["emit_docs_files", "EpicsDocsOptions"]
