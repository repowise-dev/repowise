"""MCP coverage for live commit/range change-risk scoring."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


@pytest.mark.asyncio
async def test_get_change_risk_honors_riskignore_and_request_filters(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "docs/notes.md": "notes\n",
        },
        "feat: app",
    )
    (repo / ".riskignore").write_text("tests/\n", encoding="utf-8")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    # Explicit revspec, so the uncommitted .riskignore does not become the
    # subject of the score.
    result = await module.get_change_risk(
        "HEAD", extensions=["py", "md"], exclude_patterns=["docs/"], baseline=0
    )

    assert "working_tree" not in result
    # Raw model mechanics are a projection now; the default leads with action.
    assert "features" not in result
    assert "drivers" not in result
    assert "risk_authority" not in result
    assert result["directive"]["status"] in {
        "review_required",
        "review_recommended",
        "clear_in_analyzed_scope",
        "unknown",
    }
    diagnostics = await module.get_change_risk(
        "HEAD",
        extensions=["py", "md"],
        exclude_patterns=["docs/"],
        baseline=0,
        include=["diagnostics"],
    )
    assert diagnostics["features"] == {
        "la": 1,
        "ld": 0,
        "nf": 1,
        "nd": 1,
        "ns": 1,
        "entropy": 0.0,
        "exp": 1,
    }
    assert diagnostics["risk_authority"]["primary_fields"] == ["risk_percentile"]
    assert result["exclude_patterns"] == ["tests/", "docs/"]
    assert result["risk_percentile"] is None
    # The tercile and its label repeat the percentile, so they wait for scales.
    assert "review_priority" not in result
    assert "classification" not in result
    assert diagnostics["fallback_band"] in {"low", "moderate", "high"}
    assert diagnostics["baseline_sample_size"] == 0
    # The per-field dictionary is identical on every call, so it is opt-in.
    assert "risk_scales" not in result
    expanded = await module.get_change_risk(
        "HEAD", extensions=["py", "md"], exclude_patterns=["docs/"], baseline=0, include=["scales"]
    )
    scales = {scale["field"]: scale for scale in expanded["risk_scales"]}
    assert scales["score"]["authoritative"] is False
    assert scales["risk_percentile"]["authoritative"] is True
    assert scales["classification"]["authoritative"] is False
    assert expanded["review_priority"] is None
    assert scales["features.la|features.ld"]["unit"] == "lines"
    assert scales["drivers[].contribution"]["unit"] == "logit_points"
    # Live-git responses carry a _meta envelope flagged as index-independent.
    assert result["_meta"]["source"] == "live_git"
    assert "warning" not in result


@pytest.mark.asyncio
async def test_get_change_risk_labels_the_priority_tercile_as_diff_size(tmp_path, monkeypatch):
    """The tercile ranks diff shape, so its label names size rather than a verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "value = 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "value = 2\n"}, "feat: app")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    real_payload = module.change_risk_payload

    def _ranked(result, **kwargs):
        payload = real_payload(result, **kwargs)
        payload.update(review_priority="high", classification="Elevated")
        return payload

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    monkeypatch.setattr(module, "change_risk_payload", _ranked)
    result = await module.get_change_risk("HEAD", baseline=0, include=["scales"])

    assert result["review_priority"] == "high"
    assert result["classification"] == "Above-typical diff size"


@pytest.mark.asyncio
async def test_get_change_risk_bad_revspec_returns_error(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(revspec="does-not-exist", baseline=0)

    # A bogus revspec must surface an error, not a silent zero-risk score.
    assert "error" in result
    assert "does-not-exist" in result["error"]
    assert "score" not in result


@pytest.mark.asyncio
async def test_get_change_risk_empty_diff_warns(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"app.py": "value = 1\n"}, "feat: app")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    # Only a .py change exists; restricting to .md counts zero files.
    result = await module.get_change_risk(extensions=["md"], baseline=0)

    assert result["status"] == "nothing_to_score"
    assert "score" not in result
    assert "no counted file changes" in result["warning"].lower()
    assert result["scored_repo"]["root"] == str(repo)


@pytest.mark.asyncio
async def test_get_change_risk_names_the_checkout_and_commit_it_scored(tmp_path, monkeypatch) -> None:
    """``HEAD`` resolves in the served checkout, so a worktree caller sees whose HEAD it was."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _git(["worktree", "add", "-q", "-b", "feature", str(tmp_path / "wt")], repo)
    _commit(tmp_path / "wt", {"app.py": "value = 1\n"}, "feat: on the worktree branch")
    _commit(repo, {"main.py": "value = 2\n"}, "feat: on main")

    def sha(ref: str) -> str:
        return subprocess.run(
            ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    served = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    branch = await module.get_change_risk(f"HEAD~1..{sha('feature')}", baseline=0)

    assert served["scored_repo"] == {"root": str(repo), "head": sha("main")[:12]}
    assert branch["scored_repo"] == {"root": str(repo), "head": sha("feature")[:12]}

    # Uncommitted work is marked, so its head does not read as the scored change.
    (repo / "main.py").write_text("value = 3\n", encoding="utf-8")
    dirty = await module.get_change_risk(baseline=0)
    assert dirty["scored_repo"] == {"root": str(repo), "head": sha("main")[:12], "working_tree": True}


@pytest.mark.asyncio
async def test_get_change_risk_rejects_repo_all() -> None:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    result = await module.get_change_risk(repo="all")

    assert "error" in result
    assert "get_change_risk" in result["error"]


# ---------------------------------------------------------------------------
# Line-level impacted tests (Phase 4B fold-in)
# ---------------------------------------------------------------------------


async def _factory_with_repo(
    coverage_records: list | None,
    *,
    head_commit: str | None = None,
    measured_at: str | None = None,
):
    """Build an in-memory session factory seeded with one repo + coverage map.

    *head_commit* is the commit the index describes, *measured_at* the one the
    coverage map was measured at.
    """
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence.crud import save_test_coverage
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Repository

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime(2026, 7, 18, tzinfo=UTC)
    async with factory() as s:
        s.add(
            Repository(
                id="repo1",
                name="repo",
                url="https://example.com/repo",
                local_path="/tmp/repo",
                default_branch="main",
                settings_json="{}",
                head_commit=head_commit,
                created_at=now,
                updated_at=now,
            )
        )
        await s.flush()
        if coverage_records:
            await save_test_coverage(
                s,
                "repo1",
                coverage_records,
                source_format="coverage.py",
                ingested_commit_sha=measured_at,
            )
        await s.commit()
    return factory


def _sha(repo: Path, rev: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", rev], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _tc(test_id: str, source_file: str, covered_lines: list[int], test_file: str):
    from repowise.core.analysis.health.coverage import TestCoverage

    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=covered_lines,
        source_format="coverage.py",
        test_file=test_file,
    )


@pytest.mark.asyncio
async def test_impacted_tests_line_precise_hit_and_miss(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\nb\nc\nd\ne\n"}, "chore: seed app")
    # The change: edit app.py line 3, add other.py and new.py.
    _commit(
        repo,
        {
            "src/app.py": "a\nb\nc2\nd\ne\n",
            "src/other.py": "o1\no2\no3\n",
            "src/new.py": "n1\n",
        },
        "feat: change",
    )

    factory = await _factory_with_repo(
        [
            _tc("tests/test_app.py::test_app", "src/app.py", [3], "tests/test_app.py"),
            _tc("tests/test_other.py::test_other", "src/other.py", [9], "tests/test_other.py"),
        ],
        head_commit=_sha(repo, "HEAD~1"),
        measured_at=_sha(repo),
    )

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    # The covering tests exist but are not tracked, so none runs as a test the
    # graph cannot see into; an explicit revspec keeps them out of the change.
    (repo / "tests").mkdir()
    for name in ("test_app", "test_other"):
        (repo / f"tests/{name}.py").write_text("x = 1\n", encoding="utf-8")
    result = await module.get_change_risk("HEAD", baseline=0)

    it = result["impacted_tests"]
    assert it["basis"] == "measured"
    assert it["tests_to_run_kind"] == "test_id"
    assert it["map_present"] is True
    # app.py line 3 is covered -> its test leads; other/new are not covering.
    assert it["tests_to_run"][0] == "tests/test_app.py::test_app"
    # No test is known for the new file, so the subset cannot be vouched for.
    assert it["run_all"] is True
    assert any(r.startswith("src/new.py:") for r in it["reasons"])

    mt = it["line_coverage"]
    # other.py is in the map but its changed lines (1,2,3) are uncovered.
    assert mt["untested_changes"] == [
        {"source_file": "src/other.py", "uncovered_lines": [1, 2, 3], "changed_line_count": 3}
    ]
    # new.py has no coverage rows -> unknown, never "untested".
    assert mt["no_coverage_data"] == ["src/new.py"]
    # app.py is covered but its test file is absent from the diff -> stale candidate.
    assert [s["source_file"] for s in mt["stale_test_candidates"]] == ["src/app.py"]


@pytest.mark.asyncio
async def test_impacted_tests_no_map_is_unknown_not_untested(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\nb\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a\nb\nc\n"}, "feat: add line")

    factory = await _factory_with_repo(None)  # repo present, no coverage rows

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    it = result["impacted_tests"]
    # Nothing is seeded in the graph either, so neither tier can speak.
    assert it["status"] == "run_all"
    assert it["map_present"] is False
    assert it["tests_to_run"] == []
    # Honest degradation: no untested claim, a "run every test" summary instead.
    assert "line_coverage" not in it
    assert it["summary"].startswith("Run every test:")


@pytest.mark.asyncio
async def test_impacted_tests_falls_back_to_the_graph_without_a_map(tmp_path, monkeypatch) -> None:
    """No coverage map, but the graph knows a test imports the changed file.

    Replaces a bare "run the full suite" with a candidate list on every repo
    that never ingested a report. Labelled ``inferred`` and file-level: reaching
    carries no line attribution, so ``line_coverage`` stays empty rather than
    being filled from a signal that cannot speak to lines.
    """
    from repowise.core.persistence.models import GraphEdge, GraphNode

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(
        repo,
        {"src/app.py": "a\nb\n", "tests/test_round_trips.py": "from src import app\n"},
        "chore: seed",
    )
    _commit(repo, {"src/app.py": "a\nb\nc\n"}, "feat: add line")

    factory = await _factory_with_repo(None, head_commit=_sha(repo, "HEAD~1"))
    async with factory() as s:
        for path, is_test in (("tests/test_round_trips.py", True), ("src/app.py", False)):
            s.add(
                GraphNode(
                    repository_id="repo1",
                    node_id=path,
                    node_type="file",
                    is_test=is_test,
                    always_run_reason="" if is_test else None,
                )
            )
        s.add(
            GraphEdge(
                repository_id="repo1",
                source_node_id="tests/test_round_trips.py",
                target_node_id="src/app.py",
                edge_type="imports",
            )
        )
        await s.commit()

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    assert "why" not in (await module.get_change_risk(baseline=0))["impacted_tests"]
    it = (await module.get_change_risk(baseline=0, include=["tests"]))["impacted_tests"]

    assert it["status"] == "selected"
    assert it["basis"] == "inferred"
    assert it["tests_to_run_kind"] == "test_file"
    assert it["map_present"] is False
    assert it["tests_to_run"] == ["tests/test_round_trips.py"]
    # Answered by the import tier: there is no call edge here, which is exactly
    # when the weaker tier is allowed to speak.
    assert it["why"] == {"tests/test_round_trips.py": "src/app.py changed (import-graph)"}
    assert "line_coverage" not in it
    # The only test file there is: a whole-suite selection never reads as targeted.
    assert it["summary"] == "1 test(s) to run for this change, every test file there is."


async def test_a_test_file_the_change_edits_leads_the_inferred_run_list(tmp_path, monkeypatch):
    """Twelve broad tests import both changed sources; the one test file the
    change edits imports one. It still leads, inside the ten shown."""
    from repowise.core.persistence.models import GraphEdge, GraphNode

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    generic = [f"tests/test_aaa_generic_{i:02}.py" for i in range(12)]
    edited = "tests/test_duplication.py"
    _commit(
        repo,
        {
            "src/dup.py": "a\n",
            "src/tok.py": "a\n",
            edited: "from src import dup\n",
            **dict.fromkeys(generic, "from src import dup, tok\n"),
        },
        "chore: seed",
    )
    _commit(
        repo,
        {"src/dup.py": "a\nb\n", "src/tok.py": "a\nb\n", edited: "from src import dup\n\n"},
        "feat: edit",
    )

    factory = await _factory_with_repo(None, head_commit=_sha(repo, "HEAD~1"))
    async with factory() as s:
        for path in (*generic, edited, "src/dup.py", "src/tok.py"):
            is_test = path.startswith("tests/")
            s.add(
                GraphNode(
                    repository_id="repo1",
                    node_id=path,
                    node_type="file",
                    is_test=is_test,
                    always_run_reason="" if is_test else None,
                )
            )
        imports = [(edited, "src/dup.py")]
        imports += [(t, src) for t in generic for src in ("src/dup.py", "src/tok.py")]
        for source, target in imports:
            s.add(
                GraphEdge(
                    repository_id="repo1",
                    source_node_id=source,
                    target_node_id=target,
                    edge_type="imports",
                )
            )
        await s.commit()

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    it = (await module.get_change_risk(baseline=0))["impacted_tests"]

    assert it["basis"] == "inferred"
    assert it["tests_to_run"][0] == edited
    assert len(it["tests_to_run"]) == 10


@pytest.mark.parametrize(
    ("files", "summary"),
    [
        (
            ("tests/test_a.py",),
            "2 test(s) to run for this change (1 of 4 test files); the rest can be skipped.",
        ),
        (
            ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"),
            "2 test(s) to run for this change, every test file there is.",
        ),
        # As many files as the suite holds is not the suite: one is not a known test.
        (
            ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "other/test_x.py"),
            "2 test(s) to run for this change (3 of 4 test files); the rest can be skipped.",
        ),
    ],
)
def test_a_selection_summary_says_how_much_of_the_suite_it_is(files, summary) -> None:
    from repowise.core.analysis.test_selection import Selection
    from repowise.server.mcp_server._test_selection import _summary

    selection = Selection(
        run_all=False,
        reasons=(),
        tests=("t1", "t2"),
        test_files=files,
        known_tests=("tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"),
    )
    assert _summary(selection, 2, 0, 10) == summary


@pytest.mark.asyncio
async def test_impacted_tests_overflow_cap_is_honest(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/hot.py": "seed\n"}, "chore: seed")
    _commit(repo, {"src/hot.py": "changed\n"}, "feat: touch line 1")

    # 12 distinct tests all cover the changed line 1 -> over the cap of 10.
    records = [
        _tc(f"tests/test_{i}.py::test_{i}", "src/hot.py", [1], f"tests/test_{i}.py")
        for i in range(12)
    ]
    (repo / "tests").mkdir()
    for i in range(12):
        (repo / f"tests/test_{i}.py").write_text("x = 1\n", encoding="utf-8")
    factory = await _factory_with_repo(records)

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk("HEAD", baseline=0)

    it = result["impacted_tests"]
    assert it["total"] == 12
    assert len(it["tests_to_run"]) == 10
    assert it["truncated"] is True
    # The 2 over the cap are recoverable, not dropped.
    assert "omission_marker" in result
    assert result["_meta"]["omitted"]["refs"]


@pytest.mark.asyncio
async def test_impacted_tests_no_session_factory_degrades_to_no_index(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a\nb\n"}, "feat: add")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))  # no session_factory

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    assert result["impacted_tests"]["status"] == "no_index"
    assert "map_present" not in result["impacted_tests"]


@pytest.mark.asyncio
async def test_the_two_risk_tools_do_not_share_a_key_for_different_questions() -> None:
    """``missing_tests`` asked two different questions under one name.

    get_risk's is a list of file paths with no test; this one bucketed changed
    files by line coverage. An agent that learned one misread the other.
    """
    import importlib

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    empty = module._empty_impacted("no_map", "run the suite")

    assert "missing_tests" not in empty
    # Every shape says which signal named the tests, including none.
    assert empty["basis"] == "none"
    # Nothing named, so no kind; the measured block alone carries the map
    # flag and the line buckets.
    assert "tests_to_run_kind" not in empty
    assert "map_present" not in empty
    assert "line_coverage" not in empty


def test_score_measures_names_only_the_supporting_diff_shape_signal() -> None:
    """The compatibility string stays precise while typed metadata carries authority."""
    from repowise.core.analysis.change_risk import SCORE_MEASURES

    assert "diff size and spread" in SCORE_MEASURES
    assert "where the change lands" in SCORE_MEASURES
    assert "probability" not in SCORE_MEASURES


@pytest.mark.asyncio
async def test_health_references_on_an_index_without_a_repository_are_skipped(factory) -> None:
    """An index with no repository row reads as "no index", not a failed call."""
    from repowise.server.mcp_server import tool_change_risk as tool

    finding = SimpleNamespace(
        path="a.py",
        biomarker_type="complex_method",
        symbol="f",
        line_start=1,
        line_end=2,
        health_reference=None,
    )
    await tool._attach_health_references(
        SimpleNamespace(session_factory=factory), SimpleNamespace(findings=[finding])
    )
    assert finding.health_reference is None


@pytest.mark.asyncio
async def test_get_change_risk_drops_request_echoes_and_empty_fields(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a = 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a = 1\nb = 2\n"}, "feat: add b")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk("HEAD", baseline=0)

    assert "exclude_patterns" not in result
    assert "working_tree" not in result
    assert "is_fix" not in result
    assert "density" not in result["fix_history"]
    # One percentile on the default response: the size rank, not the fix rank.
    assert "percentile" not in result["fix_history"]

    diagnostics = await module.get_change_risk("HEAD", baseline=0, include=["diagnostics"])
    assert "density" in diagnostics["fix_history"]
    assert "percentile" in diagnostics["fix_history"]


@pytest.mark.asyncio
async def test_the_default_reply_declares_its_savings_from_the_scored_file_count(
    tmp_path, monkeypatch
) -> None:
    """The file count moved behind include=["diagnostics"], which left the
    savings estimator crediting the flat floor on every default call."""
    from repowise.server.mcp_server._savings.counterfactual import change_risk_replaced_tokens

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"}, "chore: seed")
    _commit(repo, {"src/a.py": "a = 2\n", "src/b.py": "b = 2\n"}, "feat: both")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk("HEAD", baseline=0)

    assert "features" not in result
    assert result["_meta"]["replaced_tokens"] == change_risk_replaced_tokens(2)


@pytest.mark.asyncio
async def test_get_change_risk_keeps_working_tree_without_a_revspec(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a = 1\n"}, "chore: seed")
    (repo / "src" / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    assert result["working_tree"] is True


@pytest.mark.asyncio
async def test_get_change_risk_untracked_only_tree_is_one_change(tmp_path, monkeypatch) -> None:
    """Score, manifest and delta read one file set; repowise's own output drops out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / ".github").mkdir()
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_END, DISTILL_MARKER_START

    (repo / ".github" / "copilot-instructions.md").write_text(
        f"{DISTILL_MARKER_START}\nx\n{DISTILL_MARKER_END}\n", encoding="utf-8"
    )

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0, include=["diagnostics"])

    assert result["working_tree"] is True
    assert result["features"]["nf"] == 1
    assert result["health_delta"]["scope"]["changed"] == 1
    assert result["exclude_patterns"] == ["/.github/copilot-instructions.md"]


@pytest.mark.parametrize(("user_text", "selected"), [("# Mine\n", False), ("# Mine, edited\n", True)])
@pytest.mark.asyncio
async def test_impacted_tests_skip_a_managed_file_only_repowise_changed(
    tmp_path, monkeypatch, user_text, selected
) -> None:
    """A refreshed managed block leaves test selection; the user's edit beside it does not."""
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
    from repowise.core.analysis import changed_lines

    def block(body: str) -> str:
        return f"\n{DISTILL_MARKER_START}\n{body}\n{DISTILL_MARKER_END}\n"

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a = 1\n", "AGENTS.md": "# Mine\n" + block("v1")}, "chore: seed")
    (repo / "src" / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text(user_text + block("v2"), encoding="utf-8")

    seen: dict = {}
    real = changed_lines.change_set

    def _spy(*args, **kwargs):
        change = real(*args, **kwargs)
        seen["files"] = set(change.files)
        return change

    monkeypatch.setattr(changed_lines, "change_set", _spy)
    factory = await _factory_with_repo(None)
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    assert result["working_tree"] is True
    assert "src/app.py" in seen["files"]
    assert ("AGENTS.md" in seen["files"]) is selected


@pytest.mark.asyncio
async def test_get_change_risk_keeps_is_fix_when_true(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a = 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a = 2\n"}, "fix: wrong value")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk("HEAD", baseline=0)

    assert result["is_fix"] is True


@pytest.mark.asyncio
async def test_evidence_include_adds_the_checklist_and_nothing_else(tmp_path, monkeypatch) -> None:
    from repowise.core.changes.evidence import KINDS

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "def value():\n    return 1\n"}, "feat: app")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    default = await module.get_change_risk("HEAD", baseline=0)
    assert "evidence" not in default
    asked = await module.get_change_risk("HEAD", baseline=0, include=["evidence"])
    assert set(asked) - set(default) == {"evidence"}

    evidence = asked["evidence"]
    assert evidence["schema_version"] == 1 and evidence["revspec"] == "HEAD"
    by = {i["kind"]: i for i in evidence["items"]}
    assert list(by) == list(KINDS)
    # No index here: the index-backed items say so instead of reading as clear.
    assert by["owners"]["details"] == [{"reason": "the repository has no index"}]
    assert by["health_delta"]["basis"] == "measured"
    # baseline=0 ranks nothing, so the one risk row is unknown, never a verdict.
    assert by["risk_score"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_evidence_for_a_stored_change_is_its_kept_record(tmp_path, monkeypatch, factory) -> None:
    import json
    from datetime import UTC, datetime

    from sqlalchemy.exc import OperationalError

    from repowise.core.components.catalog import catalog_digest
    from repowise.core.persistence import upsert_repository
    from repowise.core.persistence.crud import upsert_changes

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "def value():\n    return 1\n"}, "feat: app (#3)")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    kept = {
        "schema_version": 1,
        "change_id": "pr-3",
        "revspec": head,
        "generated_at": "2026-10-14T00:00:00+00:00",
        "index_commit": None,
        "items": [],
        # Resolved under the catalog on disk, so components are not read again.
        "catalog_digest": catalog_digest(repo),
    }
    async with factory() as session:
        row = await upsert_repository(session, name="repo", local_path=str(repo))
        await upsert_changes(
            session,
            row.id,
            [
                {
                    "change_id": "pr-3",
                    "identity_kind": "pr",
                    "identity_value": "3",
                    "shape": "squash",
                    "title": "feat: app (#3)",
                    "merged_at": datetime(2026, 3, 1, tzinfo=UTC),
                    "head_sha": head,
                    "commit_shas_json": json.dumps([head]),
                    "evidence_json": json.dumps(kept),
                }
            ],
        )
        await session.commit()

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    async def _repo(session):
        from repowise.core.persistence.crud import get_repository_by_path

        return await get_repository_by_path(session, str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    monkeypatch.setattr(module, "_get_repo", _repo)
    # No filters of the caller's own: the same record `changes show` serves.
    same = await module.get_change_risk("HEAD", include=["evidence"])
    # Kept as it was, but for the outcomes item, which follows HEAD.
    items = same["evidence"].pop("items")
    assert same["evidence"] == {k: v for k, v in kept.items() if k != "items"}
    assert [i["kind"] for i in items] == ["outcomes"]

    async def _broken(_session):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(module, "_get_repo", _broken)
    degraded = await module.get_change_risk("HEAD", baseline=0, include=["evidence"])
    owners = {i["kind"]: i for i in degraded["evidence"]["items"]}["owners"]
    assert "database is locked" in owners["details"][0]["reason"]


async def test_a_scored_range_is_reused_until_its_inputs_move(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import update

    from repowise.core.persistence.models import Repository

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "def f():\n    return 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "def f():\n    return 2\n"}, "feat: two")
    factory = await _factory_with_repo(None, head_commit=_sha(repo))
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    scores: list[str | None] = []
    real = module.score_live_change

    def _counting(path, revspec=None, **kwargs):
        scores.append(revspec)
        return real(path, revspec, **kwargs)

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    monkeypatch.setattr(module, "score_live_change", _counting)

    first = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    again = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 1 and again["_meta"]["cached"] is True
    assert set(again["_meta"]) - {"cached"} == set(first["_meta"])
    assert {k: v for k, v in again.items() if k != "_meta"} == {
        k: v for k, v in first.items() if k != "_meta"
    }
    # A drill-down of the range just scored, known id or not, reads the kept delta.
    missing = await module.get_change_risk("HEAD~1..HEAD", baseline=0, finding_id="nope")
    assert "error" in missing and len(scores) == 1
    # Other filters are another question.
    await module.get_change_risk("HEAD~1..HEAD", baseline=0, extensions=[".py"])
    assert len(scores) == 2

    async with factory() as session:
        await session.execute(update(Repository).values(updated_at=datetime.now(UTC)))
        await session.commit()
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 3

    _commit(repo, {"src/app.py": "def f():\n    return 3\n"}, "feat: three")
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 4


def _counted_change_risk(monkeypatch, repo: Path) -> tuple[object, list, list]:
    """The tool module, with each scoring and each branch scan counted."""
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    scores: list[str | None] = []
    scans: list[str] = []
    real_score, real_scan = module.score_live_change, module._scan_from_trunk

    def _score(path, revspec=None, **kwargs):
        scores.append(revspec)
        return real_score(path, revspec, **kwargs)

    def _scan(path, files, current):
        scans.append(current)
        return real_scan(path, files, current)

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    monkeypatch.setattr(module, "score_live_change", _score)
    monkeypatch.setattr(module, "_scan_from_trunk", _scan)
    return module, scores, scans


async def test_moved_branch_tips_rebuild_only_when_the_overlap_answer_changes(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _commit(repo, {"src/app.py": "value = 1\n", "docs/notes.md": "a\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "value = 2\n"}, "feat: two")
    side = tmp_path / "side"
    _git(["worktree", "add", "-q", "-b", "side", str(side), "HEAD~1"], repo)
    module, scores, scans = _counted_change_risk(monkeypatch, repo)

    first = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 1 and "branch_overlap" not in first
    # A ref the scan never reads leaves the payload as it was, with no rescan.
    _git(["tag", "v1"], repo)
    again = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert again["_meta"]["cached"] is True and (len(scores), len(scans)) == (1, 1)

    # The branch's first commit of its own puts it in the scan's count: rebuilt.
    _commit(side, {"docs/notes.md": "b\n"}, "docs: one")
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 2
    # Another commit to a file this change does not edit: rescanned, same answer, served.
    _commit(side, {"docs/notes.md": "c\n"}, "docs: two")
    served = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert served["_meta"]["cached"] is True and len(scores) == 2 and len(scans) == 3

    # A commit to the same file changes the answer, so it is never served stale.
    _commit(side, {"src/app.py": "value = 3\n"}, "feat: side")
    rebuilt = await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 3 and "cached" not in rebuilt["_meta"]
    assert [b["branch"] for b in rebuilt["branch_overlap"]["branches"]] == ["side"]
    # The rebuild reused the scan the check already ran.
    assert len(scans) == 4


async def test_a_failed_branch_rescan_never_vouches_for_a_stored_payload(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _commit(repo, {"src/app.py": "value = 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "value = 2\n"}, "feat: two")
    module, scores, _ = _counted_change_risk(monkeypatch, repo)

    def _fails(_path, _files, _current):
        raise OSError("git went away")

    monkeypatch.setattr(module, "_scan_from_trunk", _fails)
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    _git(["branch", "side"], repo)
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 2


async def test_checkout_edits_rebuild_a_committed_range_but_repowise_writes_do_not(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "value = 1\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "value = 2\n"}, "feat: two")
    (repo / ".repowise").mkdir()
    module, scores, _ = _counted_change_risk(monkeypatch, repo)

    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    (repo / ".repowise" / "omissions.db").write_text("x", encoding="utf-8")
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 1
    # The tests block reads conftests from the checkout, so a new one is a new answer.
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    await module.get_change_risk("HEAD~1..HEAD", baseline=0)
    assert len(scores) == 2


async def test_uncommitted_work_is_never_served_from_the_cache(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "value = 1\n"}, "chore: seed")
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    scores: list[str | None] = []
    real = module.score_live_change

    def _counting(path, revspec=None, **kwargs):
        scores.append(revspec)
        return real(path, revspec, **kwargs)

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    monkeypatch.setattr(module, "score_live_change", _counting)
    (repo / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    await module.get_change_risk(baseline=0)
    second = await module.get_change_risk(baseline=0)

    assert scores == [None, None] and "cached" not in second["_meta"]


def _per_commit_repo(tmp_path, monkeypatch, commits: int):
    """A repo with a seed and *commits* one-file commits, served by the tool module."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    for n in range(commits):
        _commit(repo, {f"src/m{n}.py": "x = 1\n" * (n + 1)}, f"feat: module {n}")
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    return repo, module


@pytest.mark.asyncio
async def test_per_commit_returns_one_row_per_commit_oldest_first(tmp_path, monkeypatch) -> None:
    _repo, module = _per_commit_repo(tmp_path, monkeypatch, 3)

    result = await module.get_change_risk("HEAD~3..HEAD", baseline=0, per_commit=True)

    assert [row["subject"] for row in result["commits"]] == [
        "feat: module 0",
        "feat: module 1",
        "feat: module 2",
    ]
    assert [row["lines"] for row in result["commits"]] == ["+1 -0", "+2 -0", "+3 -0"]
    assert all(row["files"] == 1 and "fallback_band" in row for row in result["commits"])
    # The compact rows replace the review; nothing else from it rides along.
    assert "directive" not in result and "health_delta" not in result
    assert "score" not in result["commits"][0] and "more" not in result
    top = result["commits"][2]["sha"]
    assert result["review"].startswith(f"get_change_risk(revspec='{top}')")
    assert result["_meta"]["source"] == "live_git"


@pytest.mark.asyncio
async def test_per_commit_caps_rows_and_names_the_call_for_the_rest(tmp_path, monkeypatch) -> None:
    _repo, module = _per_commit_repo(tmp_path, monkeypatch, 3)
    per_commit = importlib.import_module("repowise.server.mcp_server.tool_change_risk.per_commit")
    monkeypatch.setattr(per_commit, "PER_COMMIT_LIMIT", 2)

    first = await module.get_change_risk(
        "HEAD~3..HEAD", baseline=0, per_commit=True, include=["diagnostics", "findings"]
    )

    assert len(first["commits"]) == 2 and "score" in first["commits"][0]
    last = first["commits"][-1]["sha"]
    assert first["more"] == {
        "commits": 1,
        "call": f"get_change_risk(revspec='{last}..HEAD', per_commit=True)",
    }
    assert first["ignored_arguments"][0]["values"] == ["findings"]
    rest = await module.get_change_risk(f"{last}..HEAD", baseline=0, per_commit=True)
    assert [row["subject"] for row in rest["commits"]] == ["feat: module 2"]


@pytest.mark.asyncio
async def test_per_commit_skips_merges_and_counts_them(tmp_path, monkeypatch) -> None:
    repo, module = _per_commit_repo(tmp_path, monkeypatch, 1)
    _git(["checkout", "-q", "-b", "side"], repo)
    _commit(repo, {"src/side.py": "y = 1\n"}, "feat: side")
    _git(["checkout", "-q", "-"], repo)
    _commit(repo, {"src/main.py": "z = 1\n"}, "feat: main")
    merge = ["merge", "-q", "--no-ff", "-m", "merge side", "side"]
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", *merge], repo)

    result = await module.get_change_risk("HEAD~2..HEAD", baseline=0, per_commit=True)

    assert sorted(row["subject"] for row in result["commits"]) == ["feat: main", "feat: side"]
    assert result["merges_skipped"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revspec", "finding_id", "message"),
    [
        (None, None, "per_commit needs a base..head range"),
        ("HEAD", None, "per_commit needs a base..head range"),
        ("HEAD~1..HEAD", "chf_x", "call without per_commit"),
        ("HEAD~1..--output=x", None, "cannot start with '-'"),
    ],
)
async def test_per_commit_refuses_what_it_cannot_split(
    tmp_path, monkeypatch, revspec, finding_id, message
) -> None:
    _repo, module = _per_commit_repo(tmp_path, monkeypatch, 1)

    result = await module.get_change_risk(revspec, finding_id=finding_id, per_commit=True)

    assert message in result["error"]


def test_range_commits_share_one_fix_walk_at_the_merge_base(tmp_path, monkeypatch) -> None:
    """N commits cost one history walk, read where the range starts."""
    from repowise.core.analysis.change_risk import service

    repo, _module = _per_commit_repo(tmp_path, monkeypatch, 3)
    walks: list[str] = []
    real = service._pressure_or_none

    def _counting(path, history):
        walks.append(history)
        return real(path, history)

    monkeypatch.setattr(service, "_pressure_or_none", _counting)
    scored = service.score_range_commits(str(repo), "HEAD~3..HEAD", baseline=0)

    base = subprocess.run(
        ["git", "rev-parse", "HEAD~3"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert walks == [base]
    assert scored.total == 3 and len(scored.results) == 3 and scored.merges == 0
    assert {r.history_ref for r in scored.results} == {base}


@pytest.mark.asyncio
async def test_per_commit_refuses_a_negative_baseline(tmp_path, monkeypatch) -> None:
    _repo, module = _per_commit_repo(tmp_path, monkeypatch, 1)

    result = await module.get_change_risk("HEAD~1..HEAD", baseline=-1, per_commit=True)

    assert result == {"error": "baseline must be non-negative"}
