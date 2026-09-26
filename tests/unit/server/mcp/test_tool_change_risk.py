"""How each ``get_change_risk`` enrichment degrades.

A failed git call, health comparison, index read or changed-lines read must
become a named state, never a crash or an empty list that reads as all-clear.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from repowise.server.mcp_server import _state
from repowise.server.mcp_server import tool_change_risk as tool
from repowise.server.mcp_server._budget import OmissionCollector

# ---------------------------------------------------------------------------
# get_change_risk: git failures while scoring
# ---------------------------------------------------------------------------


async def _score_raising(monkeypatch, exc: BaseException, revspec=None) -> dict:
    async def _context(_repo):
        return SimpleNamespace(path="/repo")

    def _raise(*_a, **_k):
        raise exc

    monkeypatch.setattr(tool, "_resolve_repo_context", _context)
    monkeypatch.setattr(tool, "score_live_change", _raise)
    return await tool.get_change_risk(revspec)


@pytest.mark.asyncio
async def test_git_failure_reports_the_revspec_and_git_stderr(monkeypatch):
    exc = subprocess.CalledProcessError(128, ["git"], stderr="fatal: bad object deadbeef\n")
    result = await _score_raising(monkeypatch, exc, "deadbeef")
    assert result == {"error": "Could not read change 'deadbeef': fatal: bad object deadbeef"}


@pytest.mark.asyncio
async def test_git_failure_without_stderr_falls_back_to_the_exception_text(monkeypatch):
    exc = subprocess.CalledProcessError(1, ["git", "diff"], stderr="")
    result = await _score_raising(monkeypatch, exc)
    assert result["error"].startswith("Could not read change 'HEAD': ")
    assert "returned non-zero exit status 1" in result["error"]


@pytest.mark.asyncio
async def test_git_timeout_is_an_error_naming_the_change(monkeypatch):
    result = await _score_raising(monkeypatch, subprocess.TimeoutExpired(["git"], 30), "a..b")
    assert result == {"error": "git timed out reading change 'a..b'."}


# ---------------------------------------------------------------------------
# Health comparison and its stored-finding references
# ---------------------------------------------------------------------------


def test_a_failed_health_comparison_degrades_to_unavailable(monkeypatch):
    """The comparison runs in a thread beside the enrichments; it must never raise."""

    def _broken(_path):
        raise RuntimeError("worktree vanished")

    monkeypatch.setattr(tool, "_delta_service", _broken)
    delta = tool._compare_health("/repo", "HEAD", (), ())
    assert delta.status == "unavailable"
    assert delta.explanation == "Health comparison failed: worktree vanished"
    assert delta.comparison_basis == "not_compared"
    assert delta.findings == []


def _finding(path, biomarker, symbol, start, end):
    return SimpleNamespace(
        path=path,
        biomarker_type=biomarker,
        symbol=symbol,
        line_start=start,
        line_end=end,
        health_reference=None,
    )


@pytest.mark.asyncio
async def test_only_an_exact_twin_earns_a_stored_finding_reference(factory, health_data):
    """Same file, marker, symbol and span, or no pointer at all.

    A looser match would hand the agent a ``get_health`` id for a different
    finding. The seed stores ``complex_method`` on ``authenticate`` at 10-80.
    """
    exact = _finding("src/auth/service.py", "complex_method", "authenticate", 10, 80)
    shifted = _finding("src/auth/service.py", "complex_method", "authenticate", 11, 80)
    other_file = _finding("src/db/models.py", "complex_method", "authenticate", 10, 80)
    ctx = SimpleNamespace(session_factory=factory, alias="")
    await tool._attach_health_references(
        ctx, SimpleNamespace(findings=[exact, shifted, other_file])
    )

    assert exact.health_reference["tool"] == "get_health"
    assert exact.health_reference["arguments"]["finding_id"]
    assert shifted.health_reference is None
    assert other_file.health_reference is None


@pytest.mark.asyncio
async def test_health_references_skip_the_index_when_there_is_nothing_to_match():
    finding = _finding("a.py", "complex_method", "f", 1, 2)
    await tool._attach_health_references(
        SimpleNamespace(session_factory=None), SimpleNamespace(findings=[finding])
    )
    # object() as the factory would raise if it were ever opened.
    await tool._attach_health_references(
        SimpleNamespace(session_factory=object()), SimpleNamespace(findings=[])
    )
    assert finding.health_reference is None


@pytest.mark.asyncio
async def test_health_references_on_an_index_with_no_repository_raise(factory):
    """Pins a defect: sibling helpers treat ``LookupError`` as "no index", but
    this one catches only ``SQLAlchemyError``, so an empty store raises.
    """
    finding = _finding("a.py", "complex_method", "f", 1, 2)
    with pytest.raises(LookupError):
        await tool._attach_health_references(
            SimpleNamespace(session_factory=factory), SimpleNamespace(findings=[finding])
        )


@pytest.mark.asyncio
async def test_repository_is_none_without_a_factory_or_a_repository_row(factory):
    assert await tool._repository(SimpleNamespace()) is None
    assert await tool._repository(SimpleNamespace(session_factory=factory)) is None


# ---------------------------------------------------------------------------
# Changed lines
# ---------------------------------------------------------------------------


def test_filter_changed_applies_the_scores_suffixes_and_excludes():
    changed = {"src/a.py": {1}, "src/b.ts": {2}, "docs/c.py": {3}}
    assert tool._filter_changed(changed, (".py",), ("docs/",)) == {"src/a.py": {1}}
    assert tool._filter_changed(changed, (), ()) == changed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "summary"),
    [
        (ValueError("not a ref"), "Could not read changed lines: not a ref"),
        (subprocess.SubprocessError(), "Could not read changed lines from git."),
        (OSError("git missing"), "Could not read changed lines from git."),
    ],
)
async def test_changed_lines_failure_is_unknown_not_empty(monkeypatch, exc, summary):
    import repowise.core.analysis.changed_lines as changed_lines_mod

    def _raise(*_a, **_k):
        raise exc

    monkeypatch.setattr(changed_lines_mod, "changed_lines", _raise)
    assert await tool._changed_in_scope("/repo", None, (), ()) == ({}, ("unknown", summary))


@pytest.mark.asyncio
async def test_changed_lines_all_filtered_out_is_named(monkeypatch):
    import repowise.core.analysis.changed_lines as changed_lines_mod

    monkeypatch.setattr(
        changed_lines_mod, "changed_lines", lambda *_a, **_k: ({"README.md": {1}}, "HEAD")
    )
    assert await tool._changed_in_scope("/repo", "HEAD", (".py",), ()) == (
        {},
        ("no_source_line_changes", "No changed source lines to map to tests."),
    )


# ---------------------------------------------------------------------------
# Index reads that fail
# ---------------------------------------------------------------------------


def _get_repo_raising(monkeypatch, exc: BaseException) -> None:
    async def _raise(_session, *_a, **_k):
        raise exc

    monkeypatch.setattr(tool, "_get_repo", _raise)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        LookupError("no repo"),
        OperationalError("SELECT", {}, Exception("no such table: fix_events")),
    ],
)
async def test_prior_fixes_stay_silent_when_there_is_no_record_to_read(
    monkeypatch, factory, exc
):
    _get_repo_raising(monkeypatch, exc)
    ctx = SimpleNamespace(session_factory=factory)
    assert await tool._prior_fixes_block(ctx, {"a.py": {1}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "cls_name"),
    [
        (OperationalError("SELECT", {}, Exception("database is locked")), "OperationalError"),
        (SQLAlchemyError("boom"), "SQLAlchemyError"),
    ],
)
async def test_prior_fixes_render_a_failed_read_as_unavailable(
    monkeypatch, factory, exc, cls_name
):
    _get_repo_raising(monkeypatch, exc)
    block = await tool._prior_fixes_block(
        SimpleNamespace(session_factory=factory), {"a.py": {1}}
    )
    assert block["status"] == "unavailable"
    assert cls_name in block["reason"]
    assert "not cleared" in block["summary"]


@pytest.mark.asyncio
async def test_impacted_tests_pass_a_changed_lines_error_through(factory, tmp_path):
    block = await tool._impacted_tests_block(
        SimpleNamespace(session_factory=factory),
        {},
        ("unknown", "Could not read changed lines from git."),
        OmissionCollector("get_change_risk", repo_root=tmp_path),
    )
    assert block["status"] == "unknown"
    assert block["summary"] == "Could not read changed lines from git."
    assert block["tests_to_run"] == []
    assert block["map_present"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status"),
    [(LookupError("no repo"), "no_index"), (SQLAlchemyError("boom"), "unknown")],
)
async def test_impacted_tests_degrade_on_an_unreadable_index(
    monkeypatch, factory, tmp_path, exc, status
):
    _get_repo_raising(monkeypatch, exc)
    block = await tool._impacted_tests_block(
        SimpleNamespace(session_factory=factory),
        {"a.py": {1}},
        None,
        OmissionCollector("get_change_risk", repo_root=tmp_path),
    )
    assert block["status"] == status
    assert block["total"] == 0


@pytest.mark.asyncio
async def test_inferred_tests_treat_a_failed_graph_walk_as_no_reach(
    monkeypatch, session, repo_id, tmp_path
):
    """A graph walk that throws falls back to "run the full suite", never an error."""
    import repowise.core.analysis.test_reachability as reach

    async def _raise(*_a, **_k):
        raise RuntimeError("graph unreadable")

    monkeypatch.setattr(reach, "tests_reaching", _raise)
    block = await tool._inferred_impacted(
        session, repo_id, ["a.py"], OmissionCollector("get_change_risk", repo_root=tmp_path)
    )
    assert block["status"] == "no_map"
    assert "run the full suite" in block["summary"]


@pytest.mark.asyncio
async def test_independent_changes_are_silent_for_one_file_or_an_unreadable_index(
    monkeypatch, factory, tmp_path
):
    collector = OmissionCollector("get_change_risk", repo_root=tmp_path)
    ctx = SimpleNamespace(path=str(tmp_path), session_factory=factory)
    assert await tool._independent_changes_block(ctx, {"a.py": {1}}, collector) is None

    _get_repo_raising(monkeypatch, SQLAlchemyError("boom"))
    # revspec None is not a range, so no git call is made for commit sets.
    assert (
        await tool._independent_changes_block(ctx, {"a.py": {1}, "b.py": {2}}, collector)
        is None
    )


@pytest.mark.asyncio
async def test_branch_scan_falls_back_to_git_only_when_the_index_will_not_open(
    monkeypatch, factory
):
    """Branch overlap needs only git, so an index failure costs the ranking, not the block."""
    scan = SimpleNamespace(overlap="git-only overlap")
    monkeypatch.setattr(tool, "_scan_from_trunk", lambda _path, _files: scan)
    _get_repo_raising(monkeypatch, LookupError("no repo"))
    ctx = SimpleNamespace(path="/repo", session_factory=factory)
    assert await tool._scan_overlap(ctx, ["a.py"]) == "git-only overlap"


# ---------------------------------------------------------------------------
# Cross-repo block
# ---------------------------------------------------------------------------


def _enricher(**overrides):
    base = {
        "has_contract_data": True,
        "has_breaking_changes": True,
        "get_contract_links_as_provider": lambda _alias, _path: [],
        "get_breaking_changes_for_repo": lambda _alias: [],
        "get_breaking_changes": lambda: {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cross_repo_ignores_breaking_changes_on_files_this_change_did_not_touch(monkeypatch):
    change = {
        "provider_file": "api/other.py",
        "severity": "breaking",
        "impacted_consumers": [{"repo": "web"}],
    }
    monkeypatch.setattr(tool, "_is_workspace_mode", lambda: True)
    monkeypatch.setattr(
        _state,
        "_cross_repo_enricher",
        _enricher(get_breaking_changes_for_repo=lambda _alias: [change]),
    )
    assert tool._cross_repo_block("api", ["api/orders.py"]) is None


def test_cross_repo_block_never_raises(monkeypatch):
    def _boom(_alias, _path):
        raise RuntimeError("contracts.json unreadable")

    monkeypatch.setattr(tool, "_is_workspace_mode", lambda: True)
    monkeypatch.setattr(
        _state, "_cross_repo_enricher", _enricher(get_contract_links_as_provider=_boom)
    )
    assert tool._cross_repo_block("api", ["api/orders.py"]) is None
