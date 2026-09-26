"""Shared KG curation inputs: file nodes, import edges, barrels, dominant language."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult

# Entry-point precision (plan §Phase 2). A re-export *barrel* (typically an
# ``index.ts``) carries the ``index`` stem heuristic's ``entry_point`` flag but
# teaches a reader nothing, so it is demoted in the presentation view. Runtime
# entries that survive are ranked by ``pagerank + betweenness`` and the surfaced
# set is capped — the full ranked list is kept as ``entry_candidates``.
# Only *shallow* barrels reach here now: ingestion's candidacy rule drops the
# deep ones before the flag is set. Demotion still earns its keep, because a
# package-root ``index.ts`` is a legal entry by candidacy and still a poor
# thing to lead a reader with.
_BARREL_STEMS = frozenset({"index"})
_SUBSTANTIVE_KINDS = frozenset(
    {"function", "method", "class", "struct", "interface", "enum", "trait", "impl", "macro"}
)


def _file_nodes(kg: KnowledgeGraphResult) -> list[dict]:
    """Return the file-typed nodes of *kg* (ids prefixed ``file:``)."""
    return [
        n
        for n in kg.nodes
        if isinstance(n.get("id"), str)
        and n["id"].startswith("file:")
        and isinstance(n.get("filePath"), str)
    ]


def _file_import_edges(graph_builder: Any) -> list[tuple[str, str]]:
    """``(src, dst)`` string edges from the AST graph (src imports dst).

    ``imports`` edges only (hint-sourced ones included — they are import
    semantics). The raw graph also carries ``contains`` (file → symbol),
    ``co_change``, ``calls``, and heritage edges; letting those through made
    "execution flow" claims ride on symbol counts and change-history
    coupling — a giant declarations header would out-rank every real entry
    as the walk's widest-fan-out anchor. Externals are naturally ignored
    downstream by :func:`compute_layer_order`, which only counts edges whose
    endpoints are both in ``file_layers``.
    """
    edges: list[tuple[str, str]] = []
    try:
        g = graph_builder.graph()
        for src, dst, data in g.edges(data=True):
            if not (isinstance(src, str) and isinstance(dst, str)):
                continue
            if data.get("edge_type", "imports") != "imports":
                continue
            edges.append((src, dst))
    except Exception:  # pragma: no cover - defensive
        pass
    return edges


def _is_barrel(parsed_file: Any) -> bool:
    """True if *parsed_file* is a re-export barrel (``index`` shell, no runtime).

    Conservative by design: a file is a barrel only when its stem is ``index``
    and it defines no runtime-bearing symbol (function/class/method/…) — purely
    re-exporting or empty. Anything that defines executable behaviour, even if
    named ``index``, is kept as a genuine entry candidate.
    """
    fi = getattr(parsed_file, "file_info", None)
    path = getattr(fi, "path", "")
    if PurePosixPath(path).stem.lower() not in _BARREL_STEMS:
        return False

    symbols = getattr(parsed_file, "symbols", []) or []
    if any(getattr(s, "kind", "") in _SUBSTANTIVE_KINDS for s in symbols):
        return False

    has_reexports = any(
        getattr(imp, "is_reexport", False) for imp in getattr(parsed_file, "imports", []) or []
    )
    exports_only = bool(getattr(parsed_file, "exports", []))
    return has_reexports or exports_only or not symbols


def _dominant_language(code_langs: list[str]) -> str:
    """Most common language, ties broken deterministically.

    ``Counter.most_common`` breaks count ties by insertion order, which is
    thread-completion-order nondeterministic here — and the result reaches
    persisted output (``project.graph_mode`` plus tour prose). Tie-break by
    count descending, then language name ascending, so the same KG always
    yields the same dominant language regardless of ingestion ordering.
    """
    if not code_langs:
        return ""
    return min(Counter(code_langs).items(), key=lambda kv: (-kv[1], kv[0]))[0]
