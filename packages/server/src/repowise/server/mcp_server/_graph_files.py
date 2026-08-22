"""One answer to "which files is this file a graph neighbour of".

The graph has two layers. ``imports`` and the other file-dependency types join
file paths; ``calls``, ``references``, ``extends`` and the rest join
``path::Name`` symbol nodes, and nothing points from a symbol back to its file.
A consumer that keys on file paths therefore sees only the first layer unless it
projects the second, and the projection was written twice independently
(:mod:`_flow_path`, :mod:`tool_context.enrichment`) while a third consumer had
none at all: :func:`_answer_pipeline.expand_via_graph` compared raw node ids
against ``file_page`` rows, so the call graph was invisible to retrieval
expansion.

The projection alone is not the fix. Projecting a symbol edge onto its files
manufactures a self-loop out of every intra-file call and stitches together
files a language boundary separates, so the guards travel with it here rather
than being re-derived per site.
"""

from __future__ import annotations

import os

from sqlalchemy import or_

# Confidence floor for ``calls`` edges. Imports are always 1.0; calls average
# 0.90 with a low-confidence tail from heuristic resolution. 0.5 keeps every
# genuine call and drops the noise.
CALLS_CONF_FLOOR = 0.5


def is_symbol_node(node_id: str) -> bool:
    """Whether *node_id* addresses a symbol rather than a file."""
    return "::" in node_id


def node_to_file(node_id: str) -> str:
    """``a/b.py::Klass.meth`` -> ``a/b.py``. A file node id is returned unchanged."""
    return node_id.split("::", 1)[0]


def file_ext(path: str) -> str:
    """Lower-case file extension (``retrieval.py`` -> ``py``), ``""`` if none."""
    base = os.path.basename(path)
    return base.rsplit(".", 1)[1].lower() if "." in base else ""


def keep_projected_edge(
    src_file: str, tgt_file: str, edge_type: str | None, confidence: float | None
) -> bool:
    """Whether a symbol edge projected onto its two files is a real relation.

    Three guards, all load-bearing:

    * a ``calls`` edge below :data:`CALLS_CONF_FLOOR` is heuristic noise;
    * a self-loop is what the projection manufactures out of every intra-file
      call, and those outnumber the cross-file ones;
    * a cross-extension pair is a graph coincidence rather than dependency flow
      (a Python module and a same-named TypeScript re-export, a test naming both
      ends), and keeping them lets a walk stitch unrelated packages together.
    """
    if edge_type == "calls" and (confidence or 0.0) < CALLS_CONF_FLOOR:
        return False
    if not src_file or not tgt_file or src_file == tgt_file:
        return False
    return file_ext(src_file) == file_ext(tgt_file)


def touches_files(column, paths):
    """SQLAlchemy predicate: *column* is one of *paths*, or a symbol declared in one.

    The prefix test is what reaches the symbol layer, and it is a prefix rather
    than a function on the column so the index still applies. ``autoescape``
    matters: an ordinary path is full of ``_``, which is a LIKE wildcard.
    The prefix can still over-match a longer path, so callers re-check the
    projected file in Python.
    """
    ordered = sorted(paths)
    if not ordered:
        return or_(False)
    return or_(
        column.in_(ordered),
        *[column.startswith(f"{p}::", autoescape=True) for p in ordered],
    )
