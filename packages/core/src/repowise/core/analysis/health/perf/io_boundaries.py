"""Resolve a file's imports to typed I/O boundaries (the shared half).

The performance pass needs to answer one question per call: *is this an
execution of an I/O boundary (db / network / filesystem / subprocess), or just
ordinary computation?* That decomposes into two pieces:

1. **Dependency classification** (this module) — which imported names in a file
   originate from an I/O library, and of what kind. Reuses the shared
   :func:`...ingestion.external_systems.io_kind.classify_io_kind` table
   (Primitive 1) rather than a perf-private list, so the same maintained
   classification powers C4 typing, the perf pass, and a future security layer.
   :func:`collect_io_names` walks a file's import nodes and binds each imported
   identifier to its ``io_kind``. This step is **language-agnostic** — an import
   statement is a quoted module source (TS) or a dotted path (Python/Java/...);
   either way the classifier is keyed on names.

2. **Execution-sink gating** (per language) — a *query builder* (`select()
   .where()`) is not a round-trip; only *executing* it is. That lexicon lives in
   the per-language :class:`...perf.dialects.base.PerfDialect`, because the verb
   set and the callee grammar differ by language. :meth:`PerfDialect.sink_kind`
   returns a boundary kind ONLY for execution sinks.

This module is pure: :func:`collect_io_names` takes a tree root + language tag
and returns the ``{imported_name: io_kind}`` map the dialect's ``sink_kind``
consumes. Unknown imports are simply absent (so the detector degrades to "no
signal", never a false positive).
"""

from __future__ import annotations

import re
from typing import Protocol

from ....ingestion.external_systems.io_kind import classify_io_kind

# Tokens that are syntax, not bindable import names.
_IMPORT_KW: frozenset[str] = frozenset(
    {"import", "from", "as", "require", "const", "let", "var", "default", "type", "typeof"}
)


class _NodeLike(Protocol):
    """Duck type of a tree-sitter node (avoids importing tree_sitter here)."""

    type: str
    text: bytes | None

    @property
    def children(self) -> list[_NodeLike]: ...


def _decode(node: _NodeLike) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _candidate_variants(tok: str) -> set[str]:
    """Every name a single import token could classify under.

    Two expansions, both needed for cross-language coverage and both supersets
    of the original ``split("/")[0]`` / ``split(".")[0]`` behaviour:

    * **all ``/``-segments** — so a Go module path ``github.com/go-redis/redis``
      yields ``redis`` (its interior segment), and a TS scoped package
      ``@prisma/client`` keeps resolving via its full form.
    * **progressive dotted prefixes** — so a Java FQN ``java.nio.file.Files``
      yields the dotted prefix ``java.nio.file`` (the row the JVM io_kind table
      keys on), not just the leaf ``Files`` or the root ``java``.
    """
    out: set[str] = {tok}
    parts = [p for p in tok.split("/") if p]
    out.update(parts)
    for seg in parts:
        dotted = seg.split(".")
        for i in range(1, len(dotted) + 1):
            out.add(".".join(dotted[:i]))
    return out


def _classify_import(node: _NodeLike) -> tuple[str | None, list[str]]:
    """``(io_kind, bound_names)`` for an import node, else ``(None, [])``.

    The module is classified through the shared :func:`classify_io_kind` table:
    every candidate token (a quoted TS source like ``"node:fs"`` /
    ``"@prisma/client"``, or a Python/Java dotted module's full path + interior
    segments + progressive prefixes) is tried until one resolves. When it does,
    every importable identifier in the statement is bound to that kind.
    Over-binding is deliberate and harmless: an imported name only becomes a
    finding when it is later *called as an execution sink*, which a non-I/O
    symbol never is.
    """
    text = _decode(node)
    candidates: set[str] = set()
    # TS / JS module sources are quoted string literals.
    for m in re.findall(r"""["']([^"']+)["']""", text):
        candidates |= _candidate_variants(m)
    # Python / Java / ... dotted modules / bare identifiers.
    for tok in re.findall(r"[A-Za-z0-9_.:@/]+", text):
        candidates |= _candidate_variants(tok)

    kind: str | None = None
    for cand in candidates:
        resolved = classify_io_kind(cand)
        if resolved:
            kind = resolved
            break
    if kind is None:
        return None, []

    bound = [
        t for t in re.split(r"[^A-Za-z0-9_]+", text) if t.isidentifier() and t not in _IMPORT_KW
    ]
    return kind, bound


def collect_io_names(tree_root: _NodeLike, language: str) -> dict[str, str]:
    """Map every imported identifier that resolves to an I/O library → io_kind.

    A whole-tree scan of import nodes (import statements live at module scope
    but a defensive full walk also catches function-local imports). Names that
    do not originate from a classified I/O library are simply absent.
    """
    names: dict[str, str] = {}
    stack: list[_NodeLike] = [tree_root]
    while stack:
        node = stack.pop()
        if "import" in node.type:
            kind, bound = _classify_import(node)
            if kind is not None:
                for name in bound:
                    names.setdefault(name, kind)
        for child in node.children:
            stack.append(child)
    return names
