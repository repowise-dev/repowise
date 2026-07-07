"""Shared parse + per-function metric helpers for the dataflow harnesses.

Both the CFG-only harness (:mod:`gating`) and the full def/use + reaching
harness (:mod:`analyze`) need to parse a file once and compute the two cheap
metrics the flagged-only gate keys on (``ccn`` and ``nloc``). Centralising the
fragile lazy-import + parse here keeps that error handling in one place and the
two harnesses thin. Every helper degrades to ``None`` on failure, never raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ..complexity.cyclomatic import _walk_function_body
from ..complexity.languages import LanguageNodeMap, get_language_map
from ..complexity.nloc import _count_nloc

if TYPE_CHECKING:
    from tree_sitter import Node

log = structlog.get_logger(__name__)


def parse_source(
    abs_path: str, language: str, source: bytes
) -> tuple[Node, LanguageNodeMap] | None:
    """Parse *source* once. Returns ``(root_node, lmap)`` or ``None``.

    ``None`` when the language is unsupported, the tree-sitter pack is missing,
    or parsing fails -- the same degrade-to-silence contract the walker uses.
    """
    lmap = get_language_map(language)
    if lmap is None:
        return None

    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language
    except Exception as exc:
        log.debug("dataflow_import_failed", error=str(exc))
        return None

    grammar = _get_language(language)
    if grammar is None:
        return None

    try:
        parser = Parser(grammar)
        tree = parser.parse(source)
    except Exception as exc:
        log.debug("dataflow_parse_failed", path=abs_path, error=str(exc))
        return None

    return tree.root_node, lmap


def function_metrics(fn_node: Node, lmap: LanguageNodeMap, source: bytes) -> tuple[int, int] | None:
    """Return ``(ccn, nloc)`` for *fn_node*, or ``None`` if metric extraction
    fails (so the caller can skip the function)."""
    body = fn_node.child_by_field_name("body") or fn_node
    try:
        ccn, _max_nest, _cog, _bumps, _conds = _walk_function_body(body, lmap)
        nloc = _count_nloc(body, source)
    except Exception as exc:
        log.debug("dataflow_metrics_failed", error=str(exc))
        return None
    return ccn, nloc
