"""Per-file labels rolled up the tree: technologies and containers.

A file contributes a set of labels (its code language and the frameworks it
imports; the container it belongs to). Every ancestor counts its files per
label and keeps the most common first, so a card can say "TypeScript, React,
Next" or "spans the CLI and the server" at any zoom depth without walking the
tree itself. Pure post-order aggregation, like ``metrics.py``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from repowise.core.ingestion.languages.registry import REGISTRY

from .externals import Imports
from .models import ZoomNode
from .tree import file_id

#: How many technologies a card names. More reads as a list, not a label.
TECHNOLOGY_LIMIT = 3

_CODE_LANGUAGES = REGISTRY.code_languages()


def language_label(language: str | None) -> str | None:
    """Display name of a code language; ``None`` for config, markup and data."""
    if language not in _CODE_LANGUAGES:
        return None
    spec = REGISTRY.get(language)
    return spec.display_name if spec else language


def file_technologies(
    nodes: Mapping[str, ZoomNode], imports: Imports
) -> dict[str, tuple[str, ...]]:
    """File node id -> its code language and the frameworks it imports.

    Test files count here, unlike for externals and container kinds: a test
    suite written against React is React code, but it does not ship.
    """
    frameworks: dict[str, set[str]] = {}
    for path, ext, _production in imports:
        if ext.category == "framework":
            frameworks.setdefault(file_id(path), set()).add(ext.display_name or ext.name)
    out: dict[str, tuple[str, ...]] = {}
    for nid, node in nodes.items():
        if node.kind != "file":
            continue
        lang = language_label(node.language)
        labels = ((lang,) if lang else ()) + tuple(sorted(frameworks.get(nid, ())))
        if labels:
            out[nid] = labels
    return out


def top_labels(counts: Counter[str], limit: int | None = None) -> tuple[str, ...]:
    """Most files first, ties by name so the order is stable."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(label for label, _ in ranked[:limit])


def rollup_labels(
    root_id: str,
    nodes: Mapping[str, ZoomNode],
    per_file: Mapping[str, Iterable[str]],
    limit: int | None = None,
) -> dict[str, tuple[str, ...]]:
    """Node id -> its subtree's labels, most files first (non-file nodes only).

    Iterative so a deep tree cannot hit the recursion limit.
    """
    counts: dict[str, Counter[str]] = {}
    order: list[str] = []
    stack = [root_id]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(c for c in nodes[nid].children if c in nodes)
    for nid in reversed(order):
        node = nodes[nid]
        if node.kind == "file":
            counts[nid] = Counter(per_file.get(nid, ()))
            continue
        total: Counter[str] = Counter()
        for cid in node.children:
            total.update(counts.get(cid, Counter()))
        counts[nid] = total
    return {nid: top_labels(c, limit) for nid, c in counts.items() if nodes[nid].kind != "file"}
