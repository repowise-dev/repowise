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
        {"page_type": "onboarding", "target_path": "onboarding/how_it_works"},
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


# --- `defines`: what each named file actually contains ----------------------
#
# Measured on the 25 flow questions: a get_answer response served 499 paths and
# 65 of them carried any content at all. The Layer B taxonomy judged 89% of the
# agent's post-answer searches as EXPAND, taking a name we served and going to
# fetch the substance we did not attach. `defines` is that substance, at names
# and line numbers only.


def _hit_d(path, defines, symbols=None):
    h = _hit(path, symbols)
    h["_defines"] = defines
    return h


def test_defines_names_what_the_file_declares():
    out = serialize_candidates([_hit_d("shortcuts.py", [("resolve_url", 146), ("redirect", 20)])])
    assert out == [{"path": "shortcuts.py", "defines": "resolve_url:146, redirect:20"}]


def test_a_hit_without_defines_is_unchanged():
    """The pre-change shape survives exactly where nothing was hydrated."""
    assert serialize_candidates([_hit("a.py")]) == [{"path": "a.py"}]


def test_defines_never_changes_which_paths_are_served_or_their_order():
    """HARD CONSTRAINT: Layer A Coverage scores WHICH files come back.

    Two gate fixes (#1284, #1289) bought +0.216 File Coverage and 10-of-10
    ceiling recovery on the dev half, in this same tool. `defines` is attached
    to entries the existing loop already built, so it cannot add, drop or
    reorder a path -- and this asserts that on the same pool twice rather than
    trusting the argument. An end-to-end version of the same assertion runs over
    1,392 real candidate paths in
    `50-results/payload-substance/scripts/assert_same_retrieval.py`.
    """
    bare = [_hit("a.py", [{"start_line": 3, "end_line": 9}]), _hit("b.py"), _hit("a.py::X")]
    enriched = [
        _hit_d("a.py", [("f", 3)], [{"start_line": 3, "end_line": 9}]),
        _hit_d("b.py", [("g", 1), ("h", 2)]),
        _hit_d("a.py::X", [("X", 3)]),
    ]
    got = serialize_candidates(enriched)
    stripped = [{k: v for k, v in e.items() if k != "defines"} for e in got]
    assert stripped == serialize_candidates(bare)


def test_the_char_budget_is_spent_in_rank_order_and_truncates_nothing():
    """Size discipline, and the failure mode it must not have.

    Our payload is already the larger context (13,958 chars against a bare
    agent's 12,735) and scores lower, so this block is hard-capped. The cap
    drops whole `defines` values off the tail, best-ranked file first served; it
    never truncates one mid-string, because a half-written `name:li` is a symbol
    that does not exist. And a dropped `defines` must still leave its path.
    """
    big = [("s" * 40 + str(i), i) for i in range(6)]
    out = serialize_candidates([_hit_d(f"f{i}.py", big) for i in range(30)], limit=20)

    assert [e["path"] for e in out] == [f"f{i}.py" for i in range(20)]
    assert out[0].get("defines"), "the best-ranked candidate must always be described"
    assert not out[-1].get("defines"), "the budget must run out before the tail"
    for e in out:
        if e.get("defines"):
            assert all(":" in pair for pair in e["defines"].split(", "))
