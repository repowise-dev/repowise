"""``candidates``: the navigation block get_answer returns unconditionally.

``retrieval`` is evidence and shrinks as confidence rises, which left a
high-confidence answer naming no file at all (finding A10). ``candidates`` is
the other job (where to look next), so it is not confidence-gated and it is
built from the pool before the 5-hit synthesis cap.
"""

from __future__ import annotations

from repowise.server.mcp_server.tool_answer.retrieval import serialize_candidates


def _hit(path, symbols=None):
    h = {"target_path": path}
    if symbols is not None:
        h["symbols"] = symbols
    return h


def test_one_entry_per_file_in_rank_order():
    out = serialize_candidates([_hit("a.py"), _hit("b.py"), _hit("c.py")])
    assert [e["path"] for e in out] == ["a.py", "b.py", "c.py"]


def test_line_bounds_come_from_hydrated_symbols_only():
    hits = [
        _hit("a.py", [{"start_line": 10, "end_line": 40}, {"start_line": 60, "end_line": 88}]),
        _hit("b.py"),
    ]
    out = serialize_candidates(hits)
    assert out[0] == {"path": "a.py", "lines": "10-88"}
    assert out[1] == {"path": "b.py"}  # nothing is fetched to fill this in


def test_duplicate_paths_collapse():
    out = serialize_candidates([_hit("a.py"), _hit("a.py"), _hit("b.py")])
    assert [e["path"] for e in out] == ["a.py", "b.py"]


def test_pathless_hits_are_skipped():
    out = serialize_candidates([{"target_path": ""}, {"title": "no path"}, _hit("a.py")])
    assert [e["path"] for e in out] == ["a.py"]


def test_capped():
    out = serialize_candidates([_hit(f"f{i}.py") for i in range(50)], limit=20)
    assert len(out) == 20


def test_empty_pool_yields_no_block():
    assert serialize_candidates([]) == []


def test_a_symbol_page_contributes_its_file_not_its_page_id():
    """``file.py::Symbol`` is a page identifier; a candidate must be openable.

    Measured cost of getting this wrong: on the rung 8 ContextBench dev split,
    39% of the `repowise-search` arm's served paths carried `::`, and 22 of 70
    instances surfaced a gold file only once the suffix was stripped.
    """
    out = serialize_candidates([_hit("django/db/models/base.py::Model")])
    assert out == [{"path": "django/db/models/base.py"}]


def test_two_symbols_in_one_file_are_one_candidate():
    out = serialize_candidates([_hit("a.py::One"), _hit("a.py::Two"), _hit("a.py")])
    assert [e["path"] for e in out] == ["a.py"]


def test_a_page_that_names_no_file_is_not_offered_as_one():
    """Finding A15, on the answer arm.

    A module page's target_path is a structural group key and reads exactly
    like a directory, so an agent told to open it fails and nothing in the
    response says the string was never a path.
    """
    hits = [
        {"page_type": "module_page", "target_path": "pkg/cmd/release"},
        {"page_type": "onboarding", "target_path": "onboarding/guided_tour"},
        {"page_type": "scc_page", "target_path": "scc-8f21ab"},
        {"page_type": "file_page", "target_path": "pkg/cmd/release/list.go"},
    ]
    assert serialize_candidates(hits) == [{"path": "pkg/cmd/release/list.go"}]


def test_an_unhydrated_hit_is_kept_rather_than_dropped():
    """A missing page type is a bookkeeping gap, not a verdict.

    ``hydrate_hits`` fills page_type from the Page table; a hit whose row is
    missing gets an empty one. Treating that like a classified non-file page
    would lose a real file over a join that did not land.
    """
    assert serialize_candidates([_hit("a.py")]) == [{"path": "a.py"}]
