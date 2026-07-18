"""Call resolution follows Python/JS ``from x import *`` wildcard re-exports.

A package ``__init__.py`` that re-exports a subpackage with ``from .leaf import
*`` is a barrel: a name defined in ``leaf`` becomes importable straight from the
package. ``build_import_name_maps`` skips the ``*`` (it is not a binding), so the
barrel-origin chain used by call resolution has to learn the forwarded names
from the star import itself. Without that, a call to a barrel-re-exported symbol
dead-ends at the ``__init__`` (where the name is only re-exported, not defined)
and no ``calls`` edge is produced -- unless the global-unique tier happens to
rescue it. These tests pin the followed edge, including the shadowed case where
the global tier cannot help.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build_calls(tmp_path: Path, files: dict[str, str]):
    """Write files, build the graph, return the resolved ``calls`` edge set."""
    for name, src in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    trav = FileTraverser(tmp_path)
    parser = ASTParser()
    gb = GraphBuilder(repo_path=tmp_path)
    for fi in trav.traverse():
        data = Path(fi.abs_path).read_bytes()
        gb.add_file(parser.parse_file(fi, data))
    graph = gb.build()
    return {
        (src, dst)
        for src, dst, d in graph.edges(data=True)
        if d.get("edge_type") == "calls"
    }


def test_call_through_star_import_barrel_resolves(tmp_path: Path) -> None:
    """Top-level import of a barrel-re-exported name. A same-named method
    elsewhere blocks the global-unique tier, so the edge exists only if the
    ``from .leaf import *`` re-export is followed to the leaf."""
    files = {
        "pkg/leaf.py": "def helper(x):\n    return x\n",
        "pkg/__init__.py": "from pkg.leaf import *\n",
        # shadow: a second `helper` makes the name globally non-unique
        "other.py": "class Thing:\n    def helper(self, x):\n        return x\n",
        "caller.py": "from pkg import helper\n\n\ndef run():\n    return helper(1)\n",
    }
    edges = _build_calls(tmp_path, files)
    assert ("caller.py::run", "pkg/leaf.py::helper") in edges, edges
    assert ("caller.py::run", "other.py::Thing::helper") not in edges, edges


def test_star_barrel_edge_survives_global_name_shadow(tmp_path: Path) -> None:
    """A same-named method elsewhere makes the name globally non-unique, so the
    Tier-3 fallback cannot resolve the call: only following the star re-export
    produces the edge. This is the persist.py:884 condition."""
    files = {
        "pkg/leaf.py": "def helper(x):\n    return x\n",
        "pkg/__init__.py": "from pkg.leaf import *\n",
        # a second `helper` (a method) defeats the global-unique tier
        "other.py": "class Thing:\n    def helper(self, x):\n        return x\n",
        "caller.py": (
            "def run():\n"
            "    from pkg import helper\n"  # lazy import, as in persist.py
            "    return helper(1)\n"
        ),
    }
    edges = _build_calls(tmp_path, files)
    assert ("caller.py::run", "pkg/leaf.py::helper") in edges, edges
    # and it did NOT mis-resolve to the shadowing method
    assert ("caller.py::run", "other.py::Thing::helper") not in edges, edges
