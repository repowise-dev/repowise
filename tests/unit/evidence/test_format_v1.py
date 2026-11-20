"""``repowise-evidence-v1``: whole-document errors raise, bad entries are skipped and counted."""

from __future__ import annotations

import json

import pytest

from repowise.core.evidence import EVIDENCE_FORMAT, EvidenceFormatError, parse_evidence
from repowise.core.interchange import repo_relative_path

SHA = "a" * 40


def _doc(kind: str = "profile", **fields) -> str:
    base = {"format": EVIDENCE_FORMAT, "kind": kind}
    if kind == "profile":
        base.update({"value_kind": "wall", "unit": "samples", "symbols": []})
    return json.dumps({**base, **fields})


def test_a_profile_reads_its_run_fields_and_symbols() -> None:
    doc = parse_evidence(
        _doc(
            commit_sha=SHA,
            dirty=False,
            tool={"name": "pyinstrument", "version": "4.6.2"},
            total=5300,
            explained=580,
            total_samples=5300,
            skipped={"outside_repo": 812, "vendored": 3},
            symbols=[
                {"path": "src/app/cli.py", "function": "build", "line": 40, "self": 12,
                 "total": 410, "total_samples": 410},
                {"path": "src\\app\\io.py", "function": "Reader.read", "total": 90},
            ],
        )
    )
    assert (doc.kind, doc.commit_sha, doc.dirty) == ("profile", SHA, False)
    assert (doc.tool_name, doc.tool_version) == ("pyinstrument", "4.6.2")
    assert (doc.value_kind, doc.unit, doc.total, doc.explained) == ("wall", "samples", 5300, 580)
    assert doc.total_samples == 5300
    assert parse_evidence(_doc(symbols=[], total_samples=-1)).total_samples is None
    assert doc.skipped == {"outside_repo": 812, "vendored": 3}
    assert doc.units_total == 817
    first, second = doc.entries
    assert first == {
        "path": "src/app/cli.py", "function": "build", "line": 40, "self_value": 12.0,
        "total_value": 410.0, "self_samples": None, "total_samples": 410, "call_count": None,
    }
    # Unknown stays unknown: no self value, no line, never a zero.
    assert (second["path"], second["self_value"], second["line"]) == ("src/app/io.py", None, None)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not a JSON object"),
        ("[1, 2]", "not a JSON object"),
        (json.dumps({"format": "repowise-coverage-v1", "files": {}}), "expected"),
        (json.dumps({"format": EVIDENCE_FORMAT, "kind": "trace", "symbols": []}), "unknown kind"),
        (json.dumps({"format": EVIDENCE_FORMAT, "kind": "benchmark"}), "'benchmarks' list"),
        (json.dumps({"format": EVIDENCE_FORMAT, "kind": ["profile"]}), "unknown kind"),
        (json.dumps({"format": EVIDENCE_FORMAT, "kind": {"a": 1}}), "unknown kind"),
        (_doc(commit_sha="abc123"), "commit_sha"),
        (_doc(commit_sha=SHA + "\n"), "commit_sha"),
        (_doc(commit_sha="a" * 50), "commit_sha"),
        (_doc(commit_sha=40), "commit_sha"),
        (_doc(value_kind=["wall"]), "value_kind"),
        (_doc(value_kind="alloc"), "value_kind"),
        (_doc(unit=None), "needs a unit"),
    ],
)
def test_whole_document_problems_raise_with_a_hint(text: str, message: str) -> None:
    with pytest.raises(EvidenceFormatError, match=message) as caught:
        parse_evidence(text)
    assert caught.value.hint


@pytest.mark.parametrize("commit", ["b" * 64, "B" * 64, "A" * 40])
def test_a_sha256_or_uppercase_commit_is_read_lowercase(commit: str) -> None:
    assert parse_evidence(_doc(commit_sha=commit)).commit_sha == commit.lower()


def test_bad_entries_are_skipped_by_reason() -> None:
    doc = parse_evidence(
        _doc(
            symbols=[
                {"path": "/home/me/repo/src/a.py", "function": "f", "total": 1},
                {"path": "../other/a.py", "function": "f", "total": 1},
                {"path": "src/a.py", "total": 1},
                {"path": "src/a.py", "function": "f"},
                {"path": "src/a.py", "function": "f", "total": -3},
                {"path": "src/a.py", "function": "f", "line": 3, "total": 1},
                {"path": "src/a.py", "function": "f", "line": 3, "total": 2},
                "nope",
            ]
        )
    )
    assert len(doc.entries) == 1
    assert doc.skipped == {
        "absolute_path": 1,
        "outside_repo": 1,
        "no_function": 1,
        "no_value": 2,
        "duplicate": 1,
        "not_an_object": 1,
    }


def test_benchmarks_keep_only_the_statistics_given() -> None:
    doc = parse_evidence(
        _doc(
            "benchmark",
            benchmarks=[
                {"name": "test_parse[large]", "path": "benchmarks/test_parse.py",
                 "function": "test_parse", "unit": "s", "lower_is_better": True,
                 "median": 0.0123, "rounds": 20},
                {"name": "BenchmarkEncode", "allocs_per_op": 3, "bytes_per_op": 128},
                {"name": "BenchmarkNothing"},
            ],
        )
    )
    parse, encode = doc.entries
    assert parse["location"] == {
        "path": "benchmarks/test_parse.py", "function": "test_parse", "line": None,
    }
    assert (parse["median"], parse["mean"], parse["rounds"]) == (0.0123, None, 20)
    assert (encode["location"], encode["allocs_per_op"], encode["median"]) == (None, 3.0, None)
    assert doc.skipped == {"no_value": 1}


def test_test_timings_take_a_duration_or_an_outcome() -> None:
    doc = parse_evidence(
        _doc(
            "test_timing",
            tests=[
                {"id": "tests/test_a.py::test_one", "path": "tests/test_a.py",
                 "function": "test_one", "duration_s": 0.82, "outcome": "passed"},
                {"id": "tests/test_a.py::test_skip", "outcome": "skipped"},
                {"id": "tests/test_a.py::test_odd", "outcome": "flaky"},
                {"id": "tests/test_a.py::test_one", "duration_s": 0.9},
            ],
        )
    )
    assert (doc.value_kind, doc.unit) == ("wall", "s")
    first, skip = doc.entries
    assert (first["test_file"], first["duration_s"]) == ("tests/test_a.py", 0.82)
    assert (skip["duration_s"], skip["outcome"], skip["location"]) == (None, "skipped", None)
    assert doc.skipped == {"no_value": 1, "duplicate": 1}


def test_query_counts_refuse_statement_text() -> None:
    site = {"path": "src/orders/repo.py", "function": "OrderRepo.load_items", "line": 44}
    scope = {"kind": "test", "id": "tests/api/test_orders.py::test_list"}
    doc = parse_evidence(
        _doc(
            "query_count",
            queries=[
                {"scope": scope, "site": site, "statement_fp": "9f2c1a0b7d3e4f51",
                 "count": 50, "identical_max": 50},
                {"scope": scope, "site": site, "statement_fp": "SELECT * FROM orders",
                 "count": 1},
                {"scope": {"kind": "job", "id": "x"}, "site": site,
                 "statement_fp": "9f2c1a0b", "count": 1},
                {"scope": scope, "site": {"path": "/abs.py", "function": "f"},
                 "statement_fp": "9f2c1a0b", "count": 1},
            ],
        )
    )
    (entry,) = doc.entries
    assert entry["scope_id"] == "tests/api/test_orders.py::test_list"
    assert (entry["path"], entry["query_count"], entry["total_ms"]) == ("src/orders/repo.py", 50,
                                                                         None)
    assert doc.skipped == {"statement_not_digest": 1, "no_scope": 1, "absolute_path": 1}
    assert doc.unit == "count"


def test_a_query_scope_may_say_how_many_items_it_ran_over() -> None:
    site = {"path": "src/orders/repo.py", "function": "OrderRepo.load_items", "line": 44}
    entries = [
        {"scope": {"kind": "test", "id": f"t{n}", "items": n}, "site": site,
         "statement_fp": "9f2c1a0b", "count": 3}
        for n in (10, -1)
    ]
    doc = parse_evidence(_doc("query_count", queries=entries))
    # A size that is not a count is unknown, never zero; the count itself stays.
    assert [e["scope_items"] for e in doc.entries] == [10, None]


def test_fields_outside_the_format_are_never_read() -> None:
    doc = parse_evidence(
        _doc(
            command="pytest -k secret",
            env={"TOKEN": "x"},
            symbols=[{"path": "a.py", "function": "f", "total": 1, "label": "mine"}],
        )
    )
    assert "label" not in doc.entries[0]
    assert "pytest" not in repr(doc) and "TOKEN" not in repr(doc)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/a.py", ("src/a.py", None)),
        ("./src//a.py", ("src/a.py", None)),
        ("src\\a.py", ("src/a.py", None)),
        ("/abs/a.py", (None, "absolute_path")),
        ("C:/repo/a.py", (None, "absolute_path")),
        ("src/../../a.py", (None, "outside_repo")),
        ("..", (None, "outside_repo")),
        ("", (None, "no_path")),
        (".", (None, "no_path")),
        ("src/a\x00b.py", (None, "bad_path")),
        ("src/a\tb.py", (None, "bad_path")),
        ("//server/share/a.py", (None, "absolute_path")),
        (None, (None, "no_path")),
    ],
)
def test_repo_relative_path_rules(raw, expected) -> None:
    assert repo_relative_path(raw) == expected


def test_values_and_text_that_cannot_be_stored_are_dropped() -> None:
    doc = parse_evidence(
        _doc(
            total=10**400,
            tool={"name": "pytest --token=abc", "version": "8.4.2"},
            skipped={"Outside Repo!": 2, "x" * 80: 3, "other": 1, "vendored": 4},
            symbols=[
                {"path": "a.py", "function": "f", "total": 10**400},
                {"path": "a.py", "function": "g", "total": 1, "total_samples": 2**63},
                {"path": "a.py", "function": "h" * 600, "total": 1},
                {"path": "a.py", "function": "g", "line": 9, "total": 2},
            ],
        )
    )
    assert doc.total is None
    assert (doc.tool_name, doc.tool_version) == (None, "8.4.2")
    (entry,) = doc.entries
    assert (entry["function"], entry["total_samples"]) == ("g", None)
    # Free-text reasons fold into "other" and are summed, never overwritten.
    assert doc.skipped == {
        "other": 6, "vendored": 4, "no_value": 1, "bad_name": 1, "duplicate": 1,
    }


def test_non_string_outcomes_and_scope_kinds_are_unknown_not_a_crash() -> None:
    tests = parse_evidence(
        _doc(
            "test_timing",
            tests=[
                {"id": "t::a", "outcome": {"passed": True}, "duration_s": 1.0},
                {"id": "t::b", "outcome": ["passed"]},
            ],
        )
    )
    (entry,) = tests.entries
    assert entry["outcome"] is None
    assert tests.skipped == {"no_value": 1}

    site = {"path": "src/a.py", "function": "f"}
    queries = parse_evidence(
        _doc(
            "query_count",
            queries=[{"scope": {"kind": ["test"], "id": "x"}, "site": site,
                      "statement_fp": "9f2c1a0b", "count": 1}],
        )
    )
    assert (queries.entries, queries.skipped) == ((), {"no_scope": 1})
