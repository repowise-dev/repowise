"""Context assembler for the repowise generation engine — façade.

The implementation now lives in the :mod:`repowise.core.generation.context`
package (``contexts``, ``token_budget``, ``graph_intelligence``, ``assembler``).
This module re-exports the public names so existing imports
(``from repowise.core.generation.context_assembler import ContextAssembler`` /
``FilePageContext`` / …) keep working unchanged.

ContextAssembler converts ParsedFile objects and graph metrics into context
dataclasses that are passed to Jinja2 templates as ``ctx``.
"""

from __future__ import annotations

from .context import (
    ApiContractContext,
    ArchitectureDiagramContext,
    ContextAssembler,
    FilePageContext,
    InfraPageContext,
    LayerPageContext,
    ModulePageContext,
    RepoOverviewContext,
    SccPageContext,
    SymbolSpotlightContext,
    _symbol_to_dict,
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
