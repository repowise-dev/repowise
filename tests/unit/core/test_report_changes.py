"""`repowise report changes`: the period pass, each change's answers, the
policy verdict, the summary, the renderers and the CLI, over a fixture index."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem

HEAD1, HEAD2, HEAD3 = "a" * 40, "b" * 40, "e" * 40
SINCE, UNTIL = datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC)

POLICY = """
rules:
  - name: pci-needs-tests
    match: {components: {tags: [pci]}}
    when: {reaching_tests: missing}
    action: fail
  - name: agent-review
    when: {attribution: agent}
    action: require_owner_review
  - name: scan
    gate: security
"""


def _change(cid: str, number: str, head: str, title: str, merged: datetime, category: str) -> dict:
    return {
        "change_id": cid,
        "identity_kind": "pr",
        "identity_value": number,
        "shape": "squash",
        "forge": "github",
        "forge_ref": f"#{number}",
        "title": title,
        "merged_at": merged,
        "head_sha": head,
        "commit_shas_json": json.dumps([head]),
        "files_json": json.dumps([{"path": "pay/a.py", "added": 3, "deleted": 1}]),
        "files_changed": 1,
        "lines_added": 3,
        "lines_deleted": 1,
        "category": category,
        "metadata_source": "git",
        "author_name": "Alice Example",
        "author_email": "alice@example.com",
    }


async def _seed(root: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_changes
    from repowise.core.persistence.models import GitCommit

    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="shop", local_path=str(root))
            await upsert_changes(
                session,
                repo.id,
                [
                    _change("pr-1", "1", HEAD1, '=HYPERLINK("http://x") fix', datetime(2026, 3, 1, tzinfo=UTC), "fix"),
                    _change("pr-2", "2", HEAD2, "feat: refunds", datetime(2026, 3, 5, tzinfo=UTC), "feature"),
                    _change("pr-3", "3", HEAD3, "fix: later", datetime(2026, 4, 2, tzinfo=UTC), "fix"),
                ],
            )
            session.add(
                GitCommit(
                    repository_id=repo.id,
                    sha=HEAD1,
                    author_name="Alice Example",
                    author_email="alice@example.com",
                    committed_at=datetime(2026, 3, 1, tzinfo=UTC),
                    attribution="agent",
                    agent_name="claude",
                    agent_model_id="anthropic/claude-x",
                )
            )
    finally:
        await engine.dispose()


def _gone(kind: str, label: str, reason: str) -> EvidenceItem:
    return EvidenceItem(kind, label, "unknown", "unresolved", None, [{"reason": reason}], "test")


def _evidence(change_id: str) -> ChangeEvidence:
    """pr-1 answers everything; any other change answers nothing."""
    if change_id != "pr-1":
        items = [
            _gone("components_touched", "Components touched", "no component holds the changed files"),
            _gone("owners", "Declared owners", "the repository declares no owners"),
            _gone("reaching_tests", "Tests reaching the change", "no test map"),
            _gone("provenance", "AI provenance", "no attribution was stored"),
            _gone("outcomes", "Outcomes since merge", "a shallow clone cuts off the history"),
        ]
    else:
        items = [
            EvidenceItem(
                "components_touched", "Components touched", "ok", "declared", ["payments"],
                [{"name": "payments", "source": "declared", "owner": "payments-team",
                  "criticality": "high", "tags": ["pci"], "files": ["pay/a.py"], "bus_factor": 1}],
                "components",
            ),
            EvidenceItem(
                "owners", "Declared owners", "ok", "declared", ["@acme/payments-team", "@alice"],
                [{"owner": "@acme/payments-team", "paths": ["pay/a.py"]},
                 {"owner": "@alice", "paths": ["pay/a.py"]}],
                "CODEOWNERS",
            ),
            EvidenceItem(
                "suggested_reviewers", "Suggested reviewers", "ok", "inferred", ["Bob Person"],
                [{"name": "Bob Person", "paths": ["pay/a.py"], "reasons": ["co-change"]}],
                "git history",
            ),
            EvidenceItem(
                "reaching_tests", "Tests reaching the change", "attention", "measured",
                {"measured": 0, "inferred": 0, "files_without_tests": 1, "files_unknown": 0},
                [{"path": "pay/a.py", "tests": [], "measured": True}],
                "test impact",
            ),
            EvidenceItem(
                "provenance", "AI provenance", "ok", "measured",
                {"label": "agent", "agent_commits": 1, "human_attested_commits": 0,
                 "unknown_commits": 0, "coverage": 1.0},
                [{"sha": HEAD1, "label": "agent", "tier": 2}],
                "attribution",
            ),
            EvidenceItem(
                "outcomes", "Outcomes since merge", "attention", "declared",
                {"reverted": True, "reverts": 1, "fix_references": 0, "as_of": "", "days": 30},
                [{"kind": "revert", "sha": "d" * 40, "change_id": "pr-9", "in_force": True,
                  "evidence": 'Revert "fix"'}],
                "git history",
            ),
        ]
    return ChangeEvidence(change_id, None, "2026-10-14T00:00:00+00:00", items, index_commit="c" * 40)


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the review; these tests are about the pack, not the checklist."""
    from repowise.core.changes import collect

    calls: list[str] = []

    async def build(session, repo_id, root, row, **_k):
        calls.append(row.change_id)
        return _evidence(row.change_id)

    monkeypatch.setattr(collect, "build_stored_evidence", build)
    return calls


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from repowise.cli.helpers import run_async

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = (tmp_path / "shop").resolve()
    run_async(_seed(root))
    return root


@pytest.fixture
async def ctx(root: Path):
    from repowise.core.changes.period import PeriodRequest
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Repository
    from repowise.core.report.model import ReportContext

    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    async with get_session(create_session_factory(engine)) as session:
        repo = (await session.execute(Repository.__table__.select())).first()
        yield ReportContext(session, repo.id, root, period=PeriodRequest(since=SINCE, until=UNTIL))
    await engine.dispose()


def _over(ctx, **options):
    """The period pass over the fixture window, with *options* on its request."""
    from repowise.core.changes.period import PeriodRequest, period_evidence

    request = PeriodRequest(since=SINCE, until=UNTIL, **options)
    return period_evidence(ctx.session, ctx.repository_id, ctx.repo_root, request)


async def _document(ctx) -> dict:
    from repowise.core.report.build import assemble, available_sections, collect_repo

    sections = available_sections("changes")
    doc = assemble({"kind": "repo", "name": "shop"}, [await collect_repo(ctx, sections)])
    doc["kind"] = "changes"
    return doc


def _section(doc: dict, sid: str) -> dict[str, dict]:
    (section,) = [s for s in doc["sections"] if s["id"] == sid]
    return {c["name"]: c for c in section["checks"]}


# -- the period pass ----------------------------------------------------------


async def test_period_builds_missing_checklists_once_and_keeps_them(ctx, builds):
    from repowise.core.changes.collect import cached_evidence
    from repowise.core.persistence.crud import get_change

    seen: list[tuple[int, int, str]] = []
    first = [
        r.change_id
        async for r, _e in _over(ctx, progress=lambda *a: seen.append(a),
        )
    ]
    assert first == ["pr-2", "pr-1"] and sorted(builds) == ["pr-1", "pr-2"]
    assert sorted(seen) == [(1, 2, "pr-2"), (2, 2, "pr-1")]
    kept = cached_evidence(await get_change(ctx.session, ctx.repository_id, "pr-1"))
    assert kept is not None and kept.item("outcomes") is not None

    builds.clear()
    again = [r.change_id async for r, _e in _over(ctx)]
    assert again == ["pr-2", "pr-1"] and builds == []

    _ = [r async for r in _over(ctx, refresh=True)]
    assert sorted(builds) == ["pr-1", "pr-2"]


# -- one change ---------------------------------------------------------------


def _entry(change_id: str, policy=None, policy_error=None, attribution=True) -> dict:
    from repowise.core.ownership.directory import DeclaredOwnership
    from repowise.core.report.change_answers import EntryContext, entry

    record = {
        "id": change_id, "forge_ref": "#1", "title": "t", "merged_at": "2026-03-01T00:00:00+00:00",
        "category": "fix", "head_sha": HEAD1, "commits": [HEAD1], "files_changed": 1,
        "lines_added": 3, "lines_deleted": 1,
        "metadata": {"source": "git", "reviewers": None, "approvals": None},
    }
    context = EntryContext(
        policy=policy, policy_error=policy_error,
        declared=DeclaredOwnership(codeowners_file=".github/CODEOWNERS"),
        agents={HEAD1: ("claude", "anthropic/claude-x")}, attribution=attribution,
    )
    return entry(record, _evidence(change_id).to_dict(), context)


def test_a_change_names_teams_and_counts_people():
    e = _entry("pr-1")
    assert e["accountable"]["teams"] == ["payments-team"]
    assert e["accountable"]["individual_owners"] == 1
    assert e["provenance"]["agents"] == ["claude"] and e["provenance"]["models"] == ["anthropic/claude-x"]
    assert e["outcome"]["reverted"] is True and "reverted by pr-9" in e["outcome"]["text"]
    assert e["tests"]["files_without_tests"] == 1 and e["complete"] is True
    text = json.dumps(e)
    for person in ("alice", "Alice", "Bob Person"):
        assert person not in text


def test_review_is_not_recorded_in_git_never_missing():
    from repowise.core.report.change_answers import review as _review

    e = _entry("pr-1")
    assert e["review"] == {
        "status": "unknown", "basis": "unresolved", "source": "git", "text": "not recorded in git",
    }
    trailers = _review({"source": "git", "approvals": 2, "reviewers": None})
    assert trailers["status"] == "known" and "Approved-by" in trailers["text"]
    forge = _review({"source": "acme-forge", "approvals": None, "reviewers": ["ana", "li"]})
    assert forge["reviewers"] == 2 and "ana" not in json.dumps(forge)


def test_unknown_evidence_stays_unknown_never_negative():
    from repowise.core.policy import parse_policy

    e = _entry("pr-2", policy=parse_policy(POLICY))
    for key in ("accountable", "tests", "outcome", "provenance", "review"):
        assert e[key]["status"] == "unknown", key
    assert e["policy"]["verdict"] == "unknown" and e["complete"] is False
    assert "reverted" not in e["outcome"] and e["provenance"]["commits"] == 1
    assert "not recorded" in e["review"]["text"]


def test_policy_evaluates_condition_rules_and_never_reruns_gates():
    from repowise.core.policy import parse_policy
    from repowise.core.report.change_answers import policy_verdict

    policy = parse_policy(POLICY)
    verdict = _entry("pr-1", policy=policy)["policy"]
    assert verdict["verdict"] == "fail" and verdict["gates_not_rerun"] == ["scan"]
    fired = {r["rule"]: r["outcome"] for r in verdict["rules"]}
    assert fired == {"pci-needs-tests": "triggered", "agent-review": "triggered"}
    assert policy_verdict(None, None, {})["verdict"] == "none"
    assert policy_verdict(None, "bad file", {})["verdict"] == "unknown"
    gates_only = parse_policy("rules:\n  - name: scan\n    gate: security\n")
    assert policy_verdict(gates_only, None, {})["verdict"] == "unknown"
    warn = parse_policy("rules:\n  - name: agent-review\n    when: {attribution: agent}\n    action: warn\n")
    assert _entry("pr-1", policy=warn)["policy"]["verdict"] == "warn"


# -- the report ---------------------------------------------------------------


async def test_summary_counts_the_period_by_change_and_component(ctx, builds):
    (ctx.repo_root / ".repowise" / "policies.yaml").write_text(POLICY, encoding="utf-8")
    doc = await _document(ctx)
    assert [s["id"] for s in doc["sections"]] == ["change-summary", "changes", "change-method"]
    summary = _section(doc, "change-summary")
    assert summary["changes-in-period"]["data"]["changes"] == 2
    assert summary["checklists"]["data"]["complete"] == 1
    assert summary["review-records"]["basis"] == "unresolved"
    assert summary["policy"]["data"] == {
        "configured": True, "source": ".repowise/policies.yaml",
        "pass": 0, "warn": 0, "fail": 1, "unknown": 1,
    }
    assert summary["outcomes"]["data"]["reverted"] == ["#1"]
    assert summary["components"]["data"]["components"] == {
        "payments": {"changes": 1, "teams": ["payments-team"]}
    }
    # Coverage and names, never a count or share of agent work.
    assert summary["provenance"]["data"] == {
        "commits": 2, "known": 1, "agents": ["claude"], "models": ["anthropic/claude-x"],
    }
    changes = _section(doc, "changes")
    assert set(changes) == {"#1", "#2"}
    assert changes["#2"]["basis"] == "unresolved" and changes["#2"]["score"] == -1
    assert changes["#1"]["basis"] == "measured"


async def test_no_policy_file_reads_no_policy_configured(ctx, builds):
    doc = await _document(ctx)
    assert "no policy configured" in _section(doc, "change-summary")["policy"]["reason"]
    assert all(
        c["data"]["change"]["policy"]["verdict"] == "none" for c in _section(doc, "changes").values()
    )


async def test_method_appendix_says_what_git_cannot_attest(ctx, builds):
    method = _section(await _document(ctx), "change-method")
    assert {"review", "tests", "policy", "accountability", "provenance", "outcomes",
            "snapshot", "not-attested"} == set(method)
    assert "not recorded in" in method["review"]["reason"]
    assert method["not-attested"]["basis"] == "unresolved"


async def test_renderers_present_the_same_pack(ctx, builds):
    from repowise.core.report.render import report_renderers

    doc = await _document(ctx)
    page = report_renderers.get("html")(doc)
    assert page.startswith("<!doctype html>") and "default-src 'none'" in page
    assert "<script" not in page and "Method and basis" in page and "not recorded in git" in page
    assert "Changes: shop" in report_renderers.get("markdown")(doc)

    rows = list(csv.DictReader(io.StringIO(report_renderers.get("csv")(doc))))
    by_check = {r["check"]: r for r in rows}
    assert by_check["#1"]["review"] == "not recorded in git"
    assert by_check["#1"]["teams"] == "payments-team"
    # A commit message that opens as a formula is quoted.
    assert by_check["#1"]["title"].startswith("'=HYPERLINK")
    assert by_check["#1"]["reason"].startswith("2026-03-01")
    assert by_check["changes-in-period"]["review"] == ""


def test_spreadsheet_cells_quote_every_formula_lead():
    from repowise.core.report.render import spreadsheet_cell

    for lead in ("=", "+", "-", "@", "\t", "\r"):
        assert spreadsheet_cell(f"{lead}x") == f"'{lead}x"
    assert spreadsheet_cell("fix: x") == "fix: x" and spreadsheet_cell(None) == ""


# -- CLI ------------------------------------------------------------------------


def test_cli_writes_the_pack_and_reports_progress(root: Path, builds, tmp_path: Path) -> None:
    from repowise.cli.main import cli

    out = tmp_path / "pack.json"
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        cli,
        ["report", "changes", str(root), "--no-workspace", "--since", "2026-02-01",
         "--until", "2026-04-01", "--format", "json", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "evidence 1/2: pr-2" in result.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["kind"] == "changes" and doc["schema_version"] == 1
    assert doc["scope"]["period"] == {"since": "2026-02-01T00:00:00+00:00",
                                      "until": "2026-04-01T00:00:00+00:00"}
    assert str(root) not in out.read_text(encoding="utf-8")

    as_csv = runner.invoke(cli, ["report", "changes", str(root), "--no-workspace", "--format", "csv"])
    assert as_csv.exit_code == 0 and as_csv.stdout.startswith("section,repo,check,score,basis")
    assert builds.count("pr-3") == 1  # the open period took the third change too

    bad = runner.invoke(
        cli, ["report", "changes", str(root), "--no-workspace", "--since", "2026-04-01",
              "--until", "2026-02-01"],
    )
    assert bad.exit_code != 0 and "--since must be before --until" in bad.output + bad.stderr


# -- the pass under load, failure and other writers ---------------------------


async def _add_changes(root: Path, changes: list[dict]) -> None:
    """Write *changes* through an engine of their own, as another process would."""
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path, upsert_changes

    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(root))
            await upsert_changes(session, repo.id, changes)
    finally:
        await engine.dispose()


async def _keep_elsewhere(root: Path, change_id: str, evidence: ChangeEvidence) -> None:
    from repowise.core.changes.collect import save_evidence
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path

    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(root))
            await save_evidence(session, repo.id, change_id, evidence)
    finally:
        await engine.dispose()


def _many(n: int) -> list[dict]:
    return [
        _change(f"pr-{100 + i}", str(100 + i), f"{100 + i:040x}", f"fix: {i}",
                datetime(2026, 3, 10, i % 24, tzinfo=UTC), "fix")
        for i in range(n)
    ]


async def test_many_concurrent_builds_on_disk_are_all_kept(ctx, monkeypatch):
    import asyncio

    from sqlalchemy import func, select

    from repowise.core.changes import collect
    from repowise.core.persistence.models import Change

    await _add_changes(ctx.repo_root, _many(20))
    running = peak = 0

    async def build(session, repo_id, root, row, **_k):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        # Each build reads the store on its own session while others write.
        await session.execute(select(func.count()).select_from(Change))
        await asyncio.sleep(0.01)
        running -= 1
        return _evidence(row.change_id)

    monkeypatch.setattr(collect, "build_stored_evidence", build)
    seen = []
    got = [
        r.change_id
        async for r, e in _over(ctx, concurrency=6, progress=lambda *a: seen.append(a),
        )
        if e is not None
    ]
    assert len(got) == 22 and len(set(got)) == 22 and len(seen) == 22 and peak == 6
    kept = await ctx.session.scalar(
        select(func.count()).where(Change.evidence_json.is_not(None))
    )
    assert kept == 22


async def test_a_failed_build_keeps_the_rest_of_its_page(ctx, monkeypatch):
    from repowise.core.changes import collect
    from repowise.core.persistence.crud import list_changes

    async def build(session, repo_id, root, row, **_k):
        if row.change_id == "pr-1":
            raise RuntimeError("parse blew up")
        return _evidence(row.change_id)

    monkeypatch.setattr(collect, "build_stored_evidence", build)
    with pytest.raises(RuntimeError, match="parse blew up"):
        _ = [p async for p in _over(ctx)]
    rows, _ = await list_changes(ctx.session, ctx.repository_id, limit=None)
    kept = {r.change_id for r in rows if r.evidence_json}
    assert kept == {"pr-2"}


async def test_a_checklist_another_writer_kept_is_never_overwritten(ctx, monkeypatch):
    from repowise.core.changes import collect
    from repowise.core.persistence.crud import get_change

    theirs = ChangeEvidence("pr-2", None, "2026-01-01T00:00:00+00:00",
                            [EvidenceItem("owners", "Declared owners", "ok", "declared", [], [], "server")])

    async def build(session, repo_id, root, row, **_k):
        if row.change_id == "pr-2":  # the server kept one while this build ran
            await _keep_elsewhere(root, "pr-2", theirs)
        return _evidence(row.change_id)

    monkeypatch.setattr(collect, "build_stored_evidence", build)
    got = {r.change_id: e async for r, e in _over(ctx)}
    assert got["pr-2"].generated_at == "2026-01-01T00:00:00+00:00"
    ctx.session.expunge_all()
    stored = collect.cached_evidence(await get_change(ctx.session, ctx.repository_id, "pr-2"))
    assert stored.generated_at == "2026-01-01T00:00:00+00:00"


async def test_the_period_is_fixed_before_the_first_build(ctx, monkeypatch):
    from repowise.core.changes import collect, period

    added: list[str] = []

    async def build(session, repo_id, root, row, **_k):
        # A newer change indexed mid-run must not shift the pages or repeat one.
        if not added:
            added.append(row.change_id)
            await _add_changes(root, _many(1))
        return _evidence(row.change_id)

    monkeypatch.setattr(collect, "build_stored_evidence", build)
    monkeypatch.setattr(period, "PAGE", 1)  # a page per change, so a shift would show
    got = [r.change_id async for r, _e in _over(ctx)]
    assert got == ["pr-2", "pr-1"]


def test_no_build_reports_kept_checklists_only(root: Path, builds, tmp_path: Path) -> None:
    from repowise.cli.main import cli

    out = tmp_path / "pack.json"
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        cli,
        ["report", "changes", str(root), "--no-workspace", "--since", "2026-02-01",
         "--until", "2026-04-01", "--format", "json", "--out", str(out), "--no-build"],
    )
    assert result.exit_code == 0, result.output
    assert builds == [] and "building" not in result.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    summary = _section(doc, "change-summary")
    assert summary["checklists"]["data"]["not_kept"] == 2
    change = _section(doc, "changes")["#1"]["data"]["change"]
    assert change["tests"]["status"] == "unknown" and "--no-build" in change["tests"]["text"]
    assert change["checklist"]["kept"] is False

    built = runner.invoke(cli, ["report", "changes", str(root), "--no-workspace",
                                "--since", "2026-02-01", "--until", "2026-04-01"])
    assert "building 2 of 2 evidence checklist(s)" in built.stderr
    assert "--no-build" in runner.invoke(cli, ["report", "changes", "--help"]).output


def test_a_newline_in_a_title_survives_the_csv():
    from repowise.core.report.render import render_csv

    doc = {"sections": [{"id": "changes", "repo": None, "checks": [
        {"name": "#1", "score": None, "basis": "measured", "reason": "line one\n=line two",
         "details": ["a"], "data": {"row": {"title": "fix:\nsecond line"}}},
    ]}]}
    (row,) = list(csv.DictReader(io.StringIO(render_csv(doc))))
    assert row["title"] == "fix:\nsecond line" and row["reason"] == "line one\n=line two"
