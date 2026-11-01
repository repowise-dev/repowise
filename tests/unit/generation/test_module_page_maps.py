"""Map pages read their parts' pages: write order, look-through, the empty gate."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx

from repowise.core.generation.models import compute_page_id
from repowise.core.generation.page_generator.levels import (
    _heads,
    _is_empty_module,
    _part_pages,
    _part_rows,
    module_waves,
)
from repowise.core.generation.page_generator.pertype import _SOURCES_GLUED
from repowise.core.generation.page_generator.reuse import group_material


def _g(key: str, rollup: bool = False, files: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(key=key, display=key.title(), is_rollup=rollup, file_paths=list(files))


def test_parts_are_written_before_the_maps_above_them():
    leaf, mid, top = _g("pkg/a/x"), _g("pkg/a", True), _g("pkg", True)
    assert module_waves([top, leaf, mid]) == [[leaf], [mid], [top]]
    assert module_waves([leaf]) == [[leaf]]
    # A leaf directory with its own files heads the pages below it too.
    app, js = _g("src/app", files=("src/app/main.py",)), _g("src/app/json")
    assert module_waves([app, js]) == [[js], [app]]


def test_a_thin_layer_is_looked_through_to_the_parts_below_it():
    top, src = _g("pkg", True), _g("pkg/src", True)
    kids = [_g(f"pkg/src/{n}") for n in ("io", "model", "cli", "store")]
    groups = [top, src, *kids]
    assert [g.key for g in _part_pages(top, groups, _heads(groups))] == sorted(k.key for k in kids)
    # Enough parts already: no look-through.
    wide = [_g(f"pkg/{n}") for n in ("a", "b", "c", "d")]
    groups = [top, *wide]
    assert [g.key for g in _part_pages(top, groups, _heads(groups))] == [g.key for g in wide]


def test_a_part_row_carries_how_its_page_opens_and_what_others_import():
    top, part = _g("pkg", True), _g("pkg/io", files=("pkg/io/read.py",))
    graph = nx.DiGraph()
    graph.add_edge("app/main.py", "pkg/io/read.py", edge_type="imports", imported_names=["read"])
    run = SimpleNamespace(
        sel_module_groups=[top, part],
        graph=graph,
        module_page_content={"pkg/io": "# Io\n\nReads files from disk into records.\n\n## More\n"},
        gen=SimpleNamespace(_prior_pages={}),
    )
    heads = _heads(run.sel_module_groups)
    assert _part_rows(run, top, heads) == [
        {
            "title": "Pkg/Io",
            "path": "pkg/io",
            "files": 1,
            "summary": "Reads files from disk into records.",
            "names": ["read"],
        }
    ]
    # Not written this run: the stored page answers.
    run.module_page_content = {}
    run.gen._prior_pages = {
        compute_page_id("module_page", "pkg/io"): SimpleNamespace(content="# Io\n\nStored opening.\n")
    }
    assert _part_rows(run, top, heads)[0]["summary"] == "Stored opening."


def test_a_rewritten_part_changes_the_map_pages_subject():
    parts = [{"path": "pkg/io", "summary": "Reads files."}]
    before = group_material("k", [], {}, parts)
    assert group_material("k", [], {}, parts) == before
    assert group_material("k", [], {}, [{**parts[0], "summary": "Reads sockets."}]) != before
    # A page with no parts keeps the subject it always had.
    assert group_material("k", [], {}) == group_material("k", [], {}, [])
    assert group_material("", [], {}, parts) == ""


def test_an_empty_module_gets_no_page():
    empty = SimpleNamespace(symbols=[], dependencies=[])
    code = SimpleNamespace(symbols=[{"name": "f"}], dependencies=[])
    barrel = SimpleNamespace(symbols=[], dependencies=["pkg/a.py"])
    assert _is_empty_module([empty], has_parts=False)
    assert not _is_empty_module([empty, code], has_parts=False)
    assert not _is_empty_module([barrel], has_parts=False)
    assert not _is_empty_module([empty], has_parts=True)


def test_a_sources_line_gets_its_own_paragraph():
    page = "## Step\n\nThe loader reads files.  \nSources: `a.py`\n\nNext.\n\nSources: `b.py`\n"
    assert _SOURCES_GLUED.sub("\n\n", page) == (
        "## Step\n\nThe loader reads files.\n\nSources: `a.py`\n\nNext.\n\nSources: `b.py`\n"
    )
