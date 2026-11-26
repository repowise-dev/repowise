"""``evidence.compare_history``: the OSS host lists the range from git and names
the commits that changed a reached function from function blame."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

from repowise.core.evidence.compare_history import benchmark_cause, git_range, load_comparisons
from repowise.core.persistence.models import (
    EvidenceBenchmarkResult,
    EvidenceRun,
    GitFunctionBlame,
    GraphEdge,
)
from tests.unit.persistence.helpers import insert_repo, make_wiki_symbol

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path) -> tuple[str, str, str]:
    """A checkout with a base commit, a commit that touches tree.go and one that does not."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "tree.go").write_text("package x\n\nfunc getValue() {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "tree.go").write_text("package x\n\nfunc getValue() { slow() }\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "tree: walk the path again (#9001)")
    plant = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("y\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "docs: readme")
    return base, plant, _git(tmp_path, "rev-parse", "HEAD")


def test_git_range_lists_the_commits_and_their_files(tmp_path) -> None:
    base, plant, head = _repo(tmp_path)
    assert git_range(tmp_path, base, head) == [
        (head, "docs: readme", ["README.md"]),
        (plant, "tree: walk the path again (#9001)", ["tree.go"]),
    ]
    assert git_range(tmp_path, "0" * 40, head) is None
    assert git_range(tmp_path / "missing", base, head) is None


async def _seed(session, repo_id: str, base: str, head: str, plant: str) -> None:
    session.add_all(
        [
            make_wiki_symbol(repo_id, "tree.go", "getValue", start_line=3, end_line=3),
            make_wiki_symbol(repo_id, "bench_test.go", "BenchmarkRoute", start_line=1, end_line=5),
            GitFunctionBlame(
                repository_id=repo_id,
                symbol_id="tree.go::getValue",
                file_path="tree.go",
                function_name="getValue",
                start_line=3,
                end_line=3,
                mod_count=2,
                commit_shas_json=json.dumps([plant, base]),
            ),
            GraphEdge(
                repository_id=repo_id,
                source_node_id="bench_test.go::BenchmarkRoute",
                target_node_id="tree.go::getValue",
                edge_type="calls",
            ),
        ]
    )
    for i, (commit, median) in enumerate([(base, 100), (base, 101), (base, 99), (head, 180)]):
        run_id = f"r{i}"
        session.add(
            EvidenceRun(
                id=run_id,
                repository_id=repo_id,
                run_key=run_id,
                kind="benchmark",
                format="go-bench",
                source="cli",
                workload_name="go bench",
                workload_class="benchmark",
                environment="ci",
                commit_sha=commit,
                measured_at=T0 + timedelta(hours=i),
            )
        )
        session.add(
            EvidenceBenchmarkResult(
                run_id=run_id,
                benchmark_key="pkg.BenchmarkRoute",
                repository_id=repo_id,
                symbol_id="bench_test.go::BenchmarkRoute",
                unit="ns",
                lower_is_better=True,
                median=median,
                min=median * 0.99,
                max=median * 1.01,
                allocs_per_op=0.0,
            )
        )
    await session.flush()


async def test_a_slower_benchmark_names_the_commit_that_changed_its_callee(
    async_session, tmp_path
) -> None:
    base, plant, head = _repo(tmp_path)
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id, base, head, plant)

    [found] = await load_comparisons(async_session, repo.id, tmp_path)

    assert found.verdict == "slower"
    assert found.cause is not None and found.cause.commits_in_range == 2
    [commit] = found.cause.commits
    assert (commit.sha, commit.pr, commit.functions) == (plant, 9001, ("tree.go::getValue",))

    # ``perf check`` reads the same cause for the same benchmark function.
    cause = await benchmark_cause(
        async_session, repo.id, tmp_path, base, head, ["bench_test.go::BenchmarkRoute"]
    )
    assert cause == found.cause


async def test_the_callee_walk_keeps_production_functions_nearest_first(async_session) -> None:
    """Through a test helper to the router and the tree; the helper is no reach of its own."""
    from repowise.core.evidence.compare_history import _callees

    repo = await insert_repo(async_session)
    chain = [
        ("bench_test.go::BenchmarkRoute", "bench_test.go::runRequest"),
        ("bench_test.go::runRequest", "gin.go::Engine::ServeHTTP"),
        ("bench_test.go::runRequest", "external:net/http/httptest.NewRecorder"),
        ("gin.go::Engine::ServeHTTP", "gin.go::Engine::handleHTTPRequest"),
        ("gin.go::Engine::handleHTTPRequest", "tree.go::node::getValue"),
        ("tree.go::node::getValue", "gin.go::Engine::ServeHTTP"),
        ("bench_test.go::BenchmarkJSON", "context.go::Context::JSON"),
        ("bench_test.go::BenchmarkHelperOnly", "bench_test.go::runRequest2"),
        ("bench_test.go::BenchmarkHelperOnly", "external:testing.B.ResetTimer"),
    ]
    async_session.add_all(
        GraphEdge(repository_id=repo.id, source_node_id=a, target_node_id=b, edge_type="calls")
        for a, b in chain
    )
    await async_session.flush()

    found = await _callees(
        async_session,
        repo.id,
        [
            "bench_test.go::BenchmarkRoute",
            "bench_test.go::BenchmarkJSON",
            "x_test.go::BenchmarkNone",
            "bench_test.go::BenchmarkHelperOnly",
        ],
    )

    assert found == {
        "bench_test.go::BenchmarkRoute": (
            "gin.go::Engine::ServeHTTP",
            "gin.go::Engine::handleHTTPRequest",
            "tree.go::node::getValue",
        ),
        "bench_test.go::BenchmarkJSON": ("context.go::Context::JSON",),
    }


async def test_without_a_checkout_the_slowdown_stands_without_commits(
    async_session, tmp_path
) -> None:
    base, plant, head = _repo(tmp_path)
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id, base, head, plant)

    [found] = await load_comparisons(async_session, repo.id, None)

    assert found.verdict == "slower"
    assert found.cause is not None
    assert (found.cause.commits_in_range, found.cause.commits) == (None, ())
