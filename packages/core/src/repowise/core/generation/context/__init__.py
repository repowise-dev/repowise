"""Generation context assembly.

``ContextAssembler`` converts ParsedFile objects and graph metrics into the
context dataclasses passed to the Jinja2 templates as ``ctx``. The
implementation is split across this package; import the public names from here
(or from the ``context_assembler`` façade module, which re-exports them).
"""

from __future__ import annotations

from .assembler import ContextAssembler, _symbol_to_dict
from .contexts import (
    ApiContractContext,
    ArchitectureDiagramContext,
    FilePageContext,
    InfraPageContext,
    LayerPageContext,
    ModulePageContext,
    RepoOverviewContext,
    SccPageContext,
    SymbolSpotlightContext,
    _TopFile,
)

__all__ = [
    "ApiContractContext",
    "ArchitectureDiagramContext",
    "ContextAssembler",
    "FilePageContext",
    "InfraPageContext",
    "LayerPageContext",
    "ModulePageContext",
    "RepoOverviewContext",
    "SccPageContext",
    "SymbolSpotlightContext",
    "_TopFile",
    "_symbol_to_dict",
]
