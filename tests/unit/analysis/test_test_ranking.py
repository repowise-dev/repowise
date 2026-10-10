"""The order inside a selection: tier, distance, co-change, last failure, path."""

from __future__ import annotations

from repowise.core.analysis.test_ranking import (
    Signals,
    co_change_counts,
    last_failed,
    ordered,
    rank,
    rank_selection,
    tiers_of,
)
from repowise.core.analysis.test_selection import Selection
from repowise.core.co_change import CoChangePartner


def _selection(tests: list[str], why: dict[str, str], changed: list[str]) -> Selection:
    files = list(dict.fromkeys(t.split("::", 1)[0] for t in tests))
    return Selection(
        run_all=False,
        reasons=(),
        tests=tuple(tests),
        test_files=tuple(files),
        basis=dict.fromkeys(changed, "import-graph"),
        why=why,
    )


def _row(source: str, test: str, via: str) -> dict:
    return {"source_file": source, "test_file": test, "via": via}


def test_tiers_follow_the_strength_of_the_evidence() -> None:
    result = {
        "covered": {
            f"tests/test_{kind}.py::t|run": {
                "test_file": f"tests/test_{kind}.py",
                "source_files": [source],
            }
            for kind, source in (("line", "a.py"), ("file", "b.py"))
        },
        "inferred": [
            _row("tests/test_edit.py", "tests/test_edit.py", "changed-test"),
            _row("a.py", "tests/test_call.py", "call-graph"),
            _row("a.py", "tests/test_far.py", "import-graph"),
            _row("route.py", "tests/test_route.py", "call-graph"),
        ],
    }
    tests = [
        "tests/test_line.py::t",
        "tests/test_file.py::t",
        "tests/test_edit.py",
        "tests/test_call.py",
        "tests/test_far.py",
        "tests/test_route.py",
        "tests/test_walker.py",
    ]
    why = {t.split("::")[0]: "a.py changed" for t in tests}
    why["tests/test_walker.py"] = "runs with every subset: it walks the tree"
    selection = _selection(tests, why, ["a.py", "b.py", "tests/test_edit.py"])
    tiers = tiers_of(result, selection, line_matched={"a.py"})
    assert tiers == {
        "tests/test_line.py": "line",
        "tests/test_file.py": "file",
        "tests/test_edit.py": "changed-test",
        "tests/test_call.py": "direct",
        "tests/test_far.py": "transitive",
        "tests/test_route.py": "transitive",
        "tests/test_walker.py": "every-subset",
    }
    # One import hop, once the collection measures it, is a direct edge.
    assert tiers_of(result, selection, {"a.py"}, hops={"tests/test_far.py": 1})[
        "tests/test_far.py"
    ] == "direct"
    run = [r.test for r in rank_selection(result, selection, {"a.py"}, Signals({}))]
    assert run == [
        "tests/test_edit.py",
        "tests/test_line.py::t",
        "tests/test_file.py::t",
        "tests/test_call.py",
        "tests/test_far.py",
        "tests/test_route.py",
        "tests/test_walker.py",
    ]


def test_within_a_tier_hops_then_co_change_then_failure_then_path() -> None:
    tiers = dict.fromkeys(["t/a.py", "t/b.py", "t/c.py", "t/d.py", "t/e.py"], "transitive")
    ranked = rank(
        ["t/e.py", "t/d.py", "t/c.py", "t/b.py", "t/a.py"],
        tiers,
        hops={"t/e.py": 1, "t/d.py": 2, "t/c.py": 2, "t/b.py": 2, "t/a.py": 2},
        co_change={"t/d.py": 3},
        failed={"t/c.py::test_x"},
    )
    assert [r.test for r in ranked] == ["t/e.py", "t/d.py", "t/c.py", "t/a.py", "t/b.py"]
    assert ranked[1].reason == (
        "reaches a changed file through other files; 2 hop(s); "
        "3 shared change(s) with the changed files"
    )
    assert ranked[2].failed and ranked[2].reason.endswith("failed in the last run")
    # A node id fails only on its own record, a whole file on any of its ids.
    ids = ["t/c.py::test_y", "t/c.py::test_x", "t/c.py"]
    by_test = {r.test: r.failed for r in rank(ids, tiers, failed={"t/c.py::test_x"})}
    assert by_test == {"t/c.py::test_y": False, "t/c.py::test_x": True, "t/c.py": True}
    # Without hops, distance does not order; the rest of the keys still do.
    assert [r.test for r in rank(["t/b.py", "t/a.py"], tiers)] == ["t/a.py", "t/b.py"]


def test_co_change_counts_each_pair_once_from_either_end() -> None:
    partners = {
        "src/a.py": [CoChangePartner("tests/test_a.py", 2.5, support=3)],
        "tests/test_a.py": [CoChangePartner("src/a.py", 2.5, support=3)],
        "tests/test_b.py": [
            CoChangePartner("src/a.py", 1.0, support=2),
            CoChangePartner("src/b.py", 1.0, support=1),
        ],
        # An index from before the plain count: the weight stands in.
        "tests/test_old.py": [CoChangePartner("src/a.py", 1.6)],
    }
    assert co_change_counts(["src/a.py", "src/b.py"], partners) == {
        "tests/test_a.py": 3,
        "tests/test_b.py": 3,
        "tests/test_old.py": 2,
    }


def test_last_failed_reads_pytests_cache_and_tolerates_junk() -> None:
    text = '{"tests/test_a.py::test_x": true, "tests/test_b.py": true, "gone": false}'
    assert last_failed(text) == {"tests/test_a.py::test_x", "tests/test_b.py"}
    assert last_failed(None) == frozenset()
    assert last_failed("not json") == frozenset()
    assert last_failed("[1, 2]") == frozenset()


def test_a_whole_suite_order_keeps_every_test_and_partly_selected_files() -> None:
    selection = _selection(
        ["tests/test_a.py::test_one", "tests/test_b.py"],
        {"tests/test_a.py": "a.py changed", "tests/test_b.py": "a.py changed"},
        ["a.py"],
    )
    result = {"inferred": [_row("a.py", "tests/test_b.py", "call-graph")]}
    suite = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"]
    ranked = rank_selection(
        result, selection, set(), Signals({"tests/test_d.py": 1}), everything=suite
    )
    # pytest given ``a.py::t1 a.py`` runs t1 twice, so the partly selected file
    # runs whole at its node id's place and not again in the tail.
    assert [r.test for r in ranked] == [
        "tests/test_b.py",
        "tests/test_a.py",
        "tests/test_d.py",
        "tests/test_c.py",
    ]
    whole = ordered(selection, ranked, whole=True)
    assert not whole.run_all and whole.tests == tuple(r.test for r in ranked)
    subset = ordered(selection, ranked)
    assert subset.tests == ("tests/test_b.py", "tests/test_a.py::test_one")
    assert subset.test_files == ("tests/test_b.py", "tests/test_a.py")
