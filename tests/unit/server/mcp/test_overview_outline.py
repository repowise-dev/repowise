"""get_overview serves the stored page tree.

The tree is computed once at generation time and written onto the pages; this
covers the reading side — that the outline is assembled from what is stored,
not re-derived, that it degrades honestly on a store whose tree has never been
built, and that a parent cycle cannot hang the walk.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server.tool_overview import (
    _build_outline,
    _module_order_key,
    _outline_index,
    _section_sort_key,
)


def _row(pid: str, **kw) -> SimpleNamespace:
    base = {
        "id": pid,
        "title": pid,
        "page_type": "file_page",
        "target_path": pid,
        "parent_page_id": None,
        "display_order": 0,
        "section_number": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# A spine that disagrees with the alphabet: the dependency order puts "Zebra
# Runtime" above "Alpha API". A fixture whose stored order and alphabetical
# order agree cannot tell one from the other.
ROOT = _row("repo_overview:demo", page_type="repo_overview", title="Repository Overview: demo")
LAYER_RUNTIME = _row(
    "layer_page:layer:runtime",
    page_type="layer_page",
    title="Layer: Zebra Runtime",
    target_path="layer:runtime",
    parent_page_id=ROOT.id,
    display_order=1,
    section_number="1",
)
LAYER_API = _row(
    "layer_page:layer:api",
    page_type="layer_page",
    title="Layer: Alpha API",
    target_path="layer:api",
    parent_page_id=ROOT.id,
    display_order=2,
    section_number="2",
)
MODULE = _row(
    "module_page:runtime/engine",
    page_type="module_page",
    title="Module: runtime/engine",
    target_path="runtime/engine",
    parent_page_id=LAYER_RUNTIME.id,
    display_order=1,
    section_number="1.1",
)
FILE = _row(
    "file_page:runtime/engine/loop.py",
    target_path="runtime/engine/loop.py",
    parent_page_id=MODULE.id,
    display_order=1,
    section_number="1.1.1",
)

SPINE = [ROOT, LAYER_RUNTIME, LAYER_API, MODULE, FILE]


def _collector(tmp_path) -> OmissionCollector:
    return OmissionCollector("get_overview", repo_root=tmp_path)


def test_outline_follows_the_stored_order_not_the_alphabet(tmp_path):
    outline = _build_outline(SPINE, 1, _collector(tmp_path))
    assert outline["root"]["page_id"] == ROOT.id
    assert [s["title"] for s in outline["sections"]] == [
        "Layer: Zebra Runtime",
        "Layer: Alpha API",
    ]
    assert [s["section"] for s in outline["sections"]] == ["1", "2"]
    # Guard against a fixture that would pass either way.
    assert "Layer: Zebra Runtime" > "Layer: Alpha API"


def test_outline_is_shallow_by_default_but_reports_subtree_size(tmp_path):
    outline = _build_outline(SPINE, 1, _collector(tmp_path))
    runtime = outline["sections"][0]
    assert "children" not in runtime
    # The module and the file below it are both counted, so a caller can see
    # there is more there without paying for it.
    assert runtime["descendants"] == 2
    assert outline["total_pages"] == len(SPINE)


def test_outline_opens_one_rung_deeper_on_request(tmp_path):
    outline = _build_outline(SPINE, 2, _collector(tmp_path))
    runtime = outline["sections"][0]
    assert [c["page_id"] for c in runtime["children"]] == [MODULE.id]
    # Still bounded: the module's own children are a count, not a listing.
    assert "children" not in runtime["children"][0]
    assert runtime["children"][0]["descendants"] == 1


def test_no_outline_when_the_tree_has_never_been_built(tmp_path):
    # Every parent null — what the migration leaves behind until the next
    # index or update run. A flat list dressed as a hierarchy would be a lie.
    flat = [_row("a"), _row("b"), _row("repo_overview:demo", page_type="repo_overview")]
    assert _build_outline(flat, 1, _collector(tmp_path)) == {}
    assert _outline_index(flat) == (None, {})


def test_dangling_parent_does_not_claim_a_page(tmp_path):
    orphan = _row("file_page:orphan.py", parent_page_id="module_page:deleted")
    outline = _build_outline([*SPINE, orphan], 1, _collector(tmp_path))
    # It is not a child of anything, and it is not silently promoted to a
    # top-level section either: it simply has no place in the tree. That it is
    # missing is reported rather than left for the caller to notice.
    assert orphan.id not in [s["page_id"] for s in outline["sections"]]
    assert outline["unplaced_pages"] == 1


def test_parent_cycle_terminates(tmp_path):
    a = _row("a", parent_page_id="b")
    b = _row("b", parent_page_id="a")
    outline = _build_outline([*SPINE, a, b], 2, _collector(tmp_path))
    assert [s["page_id"] for s in outline["sections"]] == [LAYER_RUNTIME.id, LAYER_API.id]
    assert outline["unplaced_pages"] == 2


def test_children_beyond_the_cap_go_to_the_omission_store(tmp_path):
    many = [
        _row(
            f"module_page:m{i:02d}",
            page_type="module_page",
            parent_page_id=LAYER_RUNTIME.id,
            display_order=i,
            section_number=f"1.{i}",
        )
        for i in range(1, 26)
    ]
    collector = _collector(tmp_path)
    outline = _build_outline([*SPINE, *many], 2, collector)
    runtime = outline["sections"][0]
    assert len(runtime["children"]) == 10
    # Truncated, not silently: the count is honest and the rest is recoverable.
    assert runtime["descendants"] == 27
    payload: dict = {}
    collector.attach(payload)
    assert payload


def test_top_level_truncation_is_declared_and_recoverable(tmp_path):
    # A repo's overview really does carry a long tail directly: this one has 53
    # cycle pages and 55 loose file pages under the root.
    tail = [
        _row(f"scc_page:s{i:03d}", page_type="scc_page", parent_page_id=ROOT.id, display_order=i)
        for i in range(1, 61)
    ]
    collector = _collector(tmp_path)
    outline = _build_outline([*SPINE, *tail], 1, collector)
    assert len(outline["sections"]) == 40
    assert outline["sections_total"] == 62
    assert outline["sections_truncated"] is True
    # The recoverable blob is what was dropped, not a superset that contradicts
    # its own "N dropped" header.
    body = "\n".join(text for _label, text in collector._chunks)
    assert "s060" in body
    assert "Layer: Zebra Runtime" not in body


def test_key_modules_lead_with_the_top_of_the_spine_not_the_alphabet():
    # get_overview caps key_modules at 8, so which 8 depends entirely on this
    # order. Alphabetically "auth" would lead; by the spine it is last.
    modules = [
        _row("m_auth", page_type="module_page", title="Module: auth", section_number="14.1"),
        _row("m_zoo", page_type="module_page", title="Module: zoo", section_number="2.1"),
        _row("m_none", page_type="module_page", title="Module: unplaced"),
    ]
    assert [m.id for m in sorted(modules, key=_module_order_key)] == [
        "m_zoo",
        "m_auth",
        "m_none",
    ]


def test_section_sort_key_is_numeric_and_puts_unplaced_last():
    ordered = sorted(["8.10", "8.9", None, "10.1", "2"], key=_section_sort_key)
    assert ordered == ["2", "8.9", "8.10", "10.1", None]
