"""``/health/coverage``: which signal answered, and how the two stay apart.

A coverage report is the measured basis. Most repositories have none, and the
route used to answer that with an empty summary - a dead end, on a question the
dependency graph can already answer. It now falls back to the graph-inferred
test map under ``basis: "inferred"``.

The tests below are mostly about the *separation* rather than the fallback. The
inferred map over-claims by construction (a call edge says control can reach the
file, not that a run did) and it has no line attribution at all, so two things
must hold however else this changes: the two bases never share a field, and no
percentage is ever derived from the inferred one. Both are asserted directly,
because both are the kind of thing a later convenience change reintroduces.
"""

from __future__ import annotations

import json

from repowise.core.persistence.crud import save_coverage_files, save_health_metrics
from repowise.core.persistence.models import GraphEdge, GraphNode

from .conftest import create_test_repo


def _metric(path: str, score: float = 5.0, nloc: int = 100) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": nloc,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": 0.0,
        "branch_coverage_pct": 0.0,
        "module": path.split("/")[0],
    }


async def _seed_graph(session, repo_id, *, nodes, edges) -> None:
    """Seed file nodes and edges. An edge is ``(src, dst, type[, origin])``."""
    for path, is_test in nodes.items():
        session.add(
            GraphNode(
                repository_id=repo_id, node_id=path, node_type="file", is_test=is_test
            )
        )
    for src, dst, etype, *origin in dict.fromkeys(
        (e[0], e[1], e[2], e[3] if len(e) > 3 else None) for e in edges
    ):
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id=src,
                target_node_id=dst,
                edge_type=etype,
                resolution_origin=origin[0] if origin else None,
            )
        )
    await session.flush()


def _calls(test_file: str, source_file: str, *, origin=None) -> list[tuple]:
    """The three edges putting one call from a test into a source file.

    Files join their symbols by ``defines`` and symbols join each other by
    ``calls``, so the shortest real path is two containment edges bridging one
    call edge.
    """
    return [
        (test_file, f"{test_file}::test_it", "defines"),
        (source_file, f"{source_file}::run", "defines"),
        (f"{test_file}::test_it", f"{source_file}::run", "calls", origin),
    ]


async def _get(client, repo_id: str, **params) -> dict:
    resp = await client.get(f"/api/repos/{repo_id}/health/coverage", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _reaching(client, repo_id: str, file_path: str) -> dict:
    resp = await client.get(
        f"/api/repos/{repo_id}/health/tests-reaching", params={"file_path": file_path}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ #
# Which basis answers
# ------------------------------------------------------------------ #


async def test_an_ingested_report_answers_as_measured(client, session, tmp_path):
    """A repo with coverage keeps the measured basis, graph or no graph."""
    repo = await create_test_repo(client, tmp_path)
    await save_coverage_files(
        session,
        repo["id"],
        [
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 40.0,
                "covered_lines": [1],
                "total_coverable_lines": 10,
                "branch_coverage_pct": None,
            }
        ],
        source_format="lcov",
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"], limit=500)

    assert body["basis"] == "measured"
    assert body["summary"]["line_coverage_pct"] == 40.0
    # The measured rows fill `files`; the inferred block (served when the
    # report missed some files) is present but has nothing to add here, because
    # every file has a measured row. `files_total` is 0, so the UI renders no
    # gap section.
    assert body["inferred"]["files_total"] == 0
    assert body["inferred"]["measured_file_count"] == 1


async def test_partial_report_answers_measured_and_fills_the_gap(
    client, session, tmp_path
):
    """A report that missed some files is measured, with the graph filling the gap.

    This is the bug in #1763: one stored row selects the measured basis for the
    whole repo, and every file the lcov report never mentioned became invisible
    on the Tests tab. The fix serves the graph-inferred answer for exactly those
    files, in a separate ``inferred`` block scoped to non-measured paths, so a
    partial ingest stops hiding the files it did not mention.
    """
    repo = await create_test_repo(client, tmp_path)
    # Only src/a.py has a measured row.
    await save_coverage_files(
        session,
        repo["id"],
        [
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 40.0,
                "covered_lines": [1],
                "total_coverable_lines": 10,
                "branch_coverage_pct": None,
            }
        ],
        source_format="lcov",
    )
    # src/b.py has no measured row but IS reached by a test; src/c.py is not.
    await save_health_metrics(
        session,
        repo["id"],
        [_metric("src/a.py"), _metric("src/b.py"), _metric("src/c.py")],
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={
            "tests/test_a.py": True,
            "src/a.py": False,
            "src/b.py": False,
            "src/c.py": False,
        },
        edges=_calls("tests/test_a.py", "src/b.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])

    # Measured basis, measured rows in `files`.
    assert body["basis"] == "measured"
    assert [f["file_path"] for f in body["files"]] == ["src/a.py"]
    assert body["summary"]["file_count"] == 1

    # The inferred block answers the non-measured files only — never both.
    assert body["inferred"]["measured_file_count"] == 1
    reached = {f["file_path"]: f["reached"] for f in body["inferred"]["files"]}
    assert reached == {"src/b.py": True, "src/c.py": False}
    assert body["inferred"]["files_total"] == 2
    assert body["inferred"]["files_reached"] == 1
    assert body["inferred"]["files_not_reached"] == 1

    # The inferred payload carries no percentage — the invariant holds on the
    # hybrid shape too.
    blob = json.dumps(body["inferred"])
    assert "pct" not in blob


async def test_the_hybrid_inferred_map_carries_no_percentage(
    client, session, tmp_path
):
    """Same no-ratio rule as the pure-inferred shape, on the hybrid block."""
    repo = await create_test_repo(client, tmp_path)
    await save_coverage_files(
        session,
        repo["id"],
        [
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 40.0,
                "covered_lines": [1],
                "total_coverable_lines": 10,
                "branch_coverage_pct": None,
            }
        ],
        source_format="lcov",
    )
    await save_health_metrics(
        session,
        repo["id"],
        [_metric("src/a.py"), _metric("src/b.py")],
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/b.py": False},
        edges=_calls("tests/test_a.py", "src/b.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])

    assert body["basis"] == "measured"
    assert "pct" not in json.dumps(body["inferred"])


async def test_no_report_falls_back_to_the_graph(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(
        session, repo["id"], [_metric("src/a.py"), _metric("src/b.py")]
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/b.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])

    assert body["basis"] == "inferred"
    reached = {f["file_path"]: f["reached"] for f in body["inferred"]["files"]}
    assert reached == {"src/a.py": True, "src/b.py": False}
    assert body["inferred"]["files_reached"] == 1
    assert body["inferred"]["files_not_reached"] == 1
    assert body["inferred"]["test_file_count"] == 1


async def test_a_repo_with_no_tests_at_all_says_none_not_inferred(
    client, session, tmp_path
):
    """``none`` is the honest unknown, distinct from "nothing reaches anything".

    With no test files there is no walk to run, so filing every file under
    "nothing reaches this" would be an assertion the graph never made.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(session, repo["id"], [_metric("src/a.py")])
    await _seed_graph(session, repo["id"], nodes={"src/a.py": False}, edges=[])
    await session.commit()

    body = await _get(client, repo["id"])

    assert body["basis"] == "none"
    assert "inferred" not in body


async def test_a_caller_can_decline_the_graph_fallback(client, session, tmp_path):
    """The tab badge asks for one row and no modules because it wants one number.

    The fallback costs a read of every call edge plus every health metric, which
    is the right price for the tab and the wrong one for the badge above it. A
    declined response omits ``basis`` rather than claiming ``none``: the graph
    was not consulted, which is not the same as the graph having nothing to say.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(session, repo["id"], [_metric("src/a.py")])
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(
        client, repo["id"], limit=1, module_limit=0, include_inferred="false"
    )

    assert "inferred" not in body
    assert "basis" not in body
    assert body["summary"]["file_count"] == 0

    # And the same repo answers in full when the tab asks.
    full = await _get(client, repo["id"])
    assert full["basis"] == "inferred"


# ------------------------------------------------------------------ #
# The two bases never share a field
# ------------------------------------------------------------------ #


async def test_the_measured_fields_stay_empty_on_the_inferred_basis(
    client, session, tmp_path
):
    """The structural half of "never merge the two".

    A consumer that reads ``files`` or ``summary`` without checking ``basis``
    gets nothing, rather than inferred rows wearing the measured shape.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(session, repo["id"], [_metric("src/a.py")])
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])

    assert body["basis"] == "inferred"
    assert body["files"] == []
    assert body["modules"] == []
    assert body["modules_total"] == 0
    assert body["summary"]["file_count"] == 0
    # Not "measured at zero": the absence of a measurement, which only ``basis``
    # can express.
    assert body["summary"]["line_coverage_pct"] is None


async def test_the_inferred_payload_carries_no_percentage_anywhere(
    client, session, tmp_path
):
    """Reaching has no line attribution, so no ratio may be derived from it.

    Asserted over the serialized payload rather than field by field: the rule is
    about anything a UI could bind a bar to, so a future field under a different
    name has to fail this too.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(
        session, repo["id"], [_metric("src/a.py"), _metric("src/b.py")]
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/b.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])
    blob = json.dumps(body["inferred"])

    assert "pct" not in blob
    assert "percent" not in blob
    assert "ratio" not in blob


# ------------------------------------------------------------------ #
# What the counts count
# ------------------------------------------------------------------ #


async def test_counts_stay_repo_wide_when_the_list_is_trimmed(
    client, session, tmp_path
):
    """``limit`` pages ``files``; it does not shrink the repo.

    Same contract as ``modules_total`` on the measured branch. The hero figure
    is built from these counts, so a trimmed page reading as the whole repo
    would understate how many files nothing tests.
    """
    repo = await create_test_repo(client, tmp_path)
    paths = [f"src/f{i}.py" for i in range(4)]
    await save_health_metrics(session, repo["id"], [_metric(p) for p in paths])
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, **{p: False for p in paths}},
        edges=_calls("tests/test_a.py", "src/f0.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"], limit=2)

    assert len(body["inferred"]["files"]) == 2
    assert body["inferred"]["files_total"] == 4
    assert body["inferred"]["files_reached"] == 1
    assert body["inferred"]["files_not_reached"] == 3


async def test_test_files_are_not_rows_in_their_own_map(client, session, tmp_path):
    """Whether a file is tested is not a question about a test.

    The forward walk never reaches a test file, so leaving them in would file
    every one of them under "nothing reaches this" and inflate the single figure
    the page leads with.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(
        session, repo["id"], [_metric("src/a.py"), _metric("tests/test_a.py")]
    )
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _get(client, repo["id"])

    assert [f["file_path"] for f in body["inferred"]["files"]] == ["src/a.py"]
    assert body["inferred"]["files_total"] == 1
    assert body["inferred"]["files_not_reached"] == 0


async def test_rows_carry_the_score_and_size_the_chart_plots(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(session, repo["id"], [_metric("src/a.py", 3.5, 240)])
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    row = (await _get(client, repo["id"]))["inferred"]["files"][0]

    assert (row["health_score"], row["nloc"]) == (3.5, 240)


# ------------------------------------------------------------------ #
# The per-file endpoint
# ------------------------------------------------------------------ #


async def test_the_file_endpoint_names_the_tests_and_the_tier(
    client, session, tmp_path
):
    repo = await create_test_repo(client, tmp_path)
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _reaching(client, repo["id"], "src/a.py")

    assert body == {
        "file_path": "src/a.py",
        "basis": "inferred",
        "reached": True,
        "tests": ["tests/test_a.py"],
        "via": "call-graph",
        "total": 1,
        "truncated": False,
    }


async def test_the_file_endpoint_marks_an_import_only_answer_as_weaker(
    client, session, tmp_path
):
    """``via`` is why the endpoint returns a tier at all.

    A test that only imports the file is real but much cruder evidence than one
    whose calls run into it, and the file page says which it has.
    """
    repo = await create_test_repo(client, tmp_path)
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=[("tests/test_a.py", "src/a.py", "imports")],
    )
    await session.commit()

    body = await _reaching(client, repo["id"], "src/a.py")

    assert body["reached"] is True
    assert body["via"] == "import-graph"
    assert body["tests"] == ["tests/test_a.py"]


async def test_a_file_nothing_reaches_answers_none_with_no_tests(
    client, session, tmp_path
):
    repo = await create_test_repo(client, tmp_path)
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/lonely.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _reaching(client, repo["id"], "src/lonely.py")

    assert body["basis"] == "none"
    assert body["reached"] is False
    assert body["tests"] == []
    assert body["via"] is None


async def test_an_unreliable_call_edge_does_not_count_as_reaching(
    client, session, tmp_path
):
    """``global_unique`` binds a name to the only symbol carrying it repo-wide.

    That is a guess, and dropping it is worth 4 points of forward precision at
    no recall cost. Asserted here as well because these routes are a second
    consumer of the filter and would not notice losing it.
    """
    repo = await create_test_repo(client, tmp_path)
    await save_health_metrics(session, repo["id"], [_metric("src/a.py")])
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py", origin="global_unique"),
    )
    await session.commit()

    body = await _get(client, repo["id"])
    assert body["inferred"]["files_reached"] == 0

    detail = await _reaching(client, repo["id"], "src/a.py")
    assert detail["basis"] == "none"


async def test_the_file_endpoint_says_when_the_list_was_cut(
    client, session, tmp_path, monkeypatch
):
    """``tests`` is capped; the count beside it must not be.

    The cut is ``sorted(tests)[:cap]``, so a surface printing ``len(tests)``
    reports the cap as the answer and shows an arbitrary alphabetical slice of
    the evidence. Measured on this repository, one file is reached by 124 tests
    and would have rendered as 50.
    """
    import repowise.core.analysis.test_reachability as tr

    monkeypatch.setattr(tr, "MAX_TESTS_PER_TARGET", 2)
    repo = await create_test_repo(client, tmp_path)
    tests = [f"tests/test_{i}.py" for i in range(5)]
    edges = [e for t in tests for e in _calls(t, "src/a.py")]
    await _seed_graph(
        session,
        repo["id"],
        nodes={**{t: True for t in tests}, "src/a.py": False},
        edges=edges,
    )
    await session.commit()

    body = await _reaching(client, repo["id"], "src/a.py")

    assert len(body["tests"]) == 2
    assert body["total"] == 5
    assert body["truncated"] is True


async def test_an_uncut_list_is_not_marked_truncated(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    await _seed_graph(
        session,
        repo["id"],
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=_calls("tests/test_a.py", "src/a.py"),
    )
    await session.commit()

    body = await _reaching(client, repo["id"], "src/a.py")

    assert body["total"] == 1
    assert body["truncated"] is False


async def test_the_file_endpoint_404s_for_an_unknown_repo(client):
    resp = await client.get(
        "/api/repos/nope/health/tests-reaching", params={"file_path": "src/a.py"}
    )
    assert resp.status_code == 404
