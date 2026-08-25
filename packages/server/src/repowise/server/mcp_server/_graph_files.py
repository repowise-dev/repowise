"""One answer to "which files is this file a graph neighbour of".

``imports`` joins file paths; ``calls`` and its siblings join ``path::Name``
symbol nodes, and nothing points from a symbol back to its file, so a consumer
keyed on paths sees only the first layer until it projects the second.

Projecting also manufactures a self-loop out of every intra-file call and
stitches files across a language boundary, so the guards live here with it.
"""

from __future__ import annotations

import os

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
