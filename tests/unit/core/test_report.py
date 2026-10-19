"""`repowise report assessment`: sections over a fixture index, the section
seam, the renderers, and the CLI."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.core.report.model import ReportContext, Section, check, report_sections

_NOW = datetime(2026, 10, 1, tzinfo=UTC)


async def _seed(root: Path) -> None:
    """A two-component index: ``api`` (Python) and ``web`` (Java, a gated language)."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import (
        DeadCodeFinding,
        GitCommit,
        GitMetadata,
        GraphNode,
        HealthFileMetric,
    )

    (root / ".repowise").mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="shop", local_path=str(root))
            repo.head_commit = "c" * 40
            rid = repo.id
            files = {"api/a.py": False, "api/b.py": False, "api/test_a.py": True, "web/W.java": False}
            session.add_all(
                GraphNode(repository_id=rid, node_id=p, node_type="file", is_test=t)
                for p, t in files.items()
            )
            owners = {"api/a.py": "Alice", "api/b.py": "Bob", "web/W.java": "Alice"}
            session.add_all(
                GitMetadata(
                    repository_id=rid,
                    file_path=p,
                    primary_owner_name=o,
                    primary_owner_email=f"{o.lower()}@example.com",
                    is_hotspot=p == "api/a.py",
                )
                for p, o in owners.items()
            )
            session.add_all(
                [
                    HealthFileMetric(
                        repository_id=rid, file_path="api/a.py", score=6.0, nloc=100,
                        has_test_file=True, line_coverage_pct=80.0,
                    ),
                    HealthFileMetric(
                        repository_id=rid, file_path="api/b.py", score=9.0, nloc=100,
                        has_test_file=False,
                    ),
                    HealthFileMetric(
                        repository_id=rid, file_path="web/W.java", score=3.0, nloc=50,
                        has_test_file=False,
                    ),
                ]
            )
            session.add_all(
                [
                    DeadCodeFinding(
                        repository_id=rid, kind="unused_export", file_path="api/b.py",
                        symbol_name="old", confidence=0.9, lines=10, status="open",
                        safe_to_delete=True,
                    ),
                    DeadCodeFinding(
                        repository_id=rid, kind="unreachable_file", file_path="web/W.java",
                        confidence=1.0, lines=50, status="open",
                    ),
                ]
            )
            labels = [
                ("agent", "claude", "anthropic/claude-x"),
                ("agent", "codex", None),
                ("human_attested", None, None),
                ("unknown", None, None),
            ]
            session.add_all(
                GitCommit(
                    repository_id=rid,
                    sha=f"{i:040d}",
                    author_name="Alice",
                    author_email="alice@example.com",
                    committed_at=_NOW - timedelta(days=i),
                    attribution=label,
                    agent_name=agent,
                    agent_model_id=model,
                )
                for i, (label, agent, model) in enumerate(labels)
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Repository

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = (tmp_path / "shop").resolve()
    await _seed(root)
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    async with get_session(create_session_factory(engine)) as session:
        repo = (await session.execute(Repository.__table__.select())).first()
        yield ReportContext(session, repo.id, root, head_commit=repo.head_commit)
    await engine.dispose()


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {c["name"]: c for c in items}


async def _section(name: str, ctx: ReportContext) -> dict[str, dict]:
    import repowise.core.report.sections  # noqa: F401

    return _by_name(await report_sections.get(name).collect(ctx))


# ---------------------------------------------------------------------------
# Evidence items
# ---------------------------------------------------------------------------


def test_check_keeps_scorecard_keys_and_forces_inconclusive_without_evidence():
    item = check("x", reason="r", basis="measured", method="m", score=12.34, details=["d"])
    assert list(item) == ["name", "score", "reason", "details", "method", "basis"]
    assert item["score"] == 10.0
    assert check("x", reason="r", basis="gated", method="m", score=7)["score"] == -1
    assert check("x", reason="r", basis="unresolved", method="m")["score"] == -1
    assert check("x", reason="r", basis="declared", method="m")["score"] is None
    with pytest.raises(ValueError):
        check("x", reason="r", basis="guessed", method="m")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


async def test_components_section_scores_health_and_marks_gated_languages(ctx, monkeypatch):
    # No shipped row is gated: run on the audit's gated table.
    from repowise.core.analysis import finding_registry
    from tests.unit.server.mcp.test_language_gates import AUDIT_GATED

    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", AUDIT_GATED)
    items = await _section("components", ctx)
    assert items["component-map"]["data"]["derived"] == 2
    summary = items["components"]
    # api has nothing to note, so it is named in the summary; its figures stay in data.
    assert "component:api" not in items
    assert summary["details"] == ["no declared owner: api"]
    assert summary["data"]["components"]["api"]["health"]["average"] == 7.5
    web = items["component:web"]  # gated evidence always keeps its own item
    assert (web["basis"], web["score"]) == ("gated", -1)
    assert items["health:gated"]["data"]["gated_files"] == 1


async def test_unknown_owner_activity_is_unresolved_never_quiet(ctx):
    (ctx.repo_root / ".github").mkdir()
    (ctx.repo_root / ".github" / "CODEOWNERS").write_text("api/ @org/ghosts\n", encoding="utf-8")
    items = await _section("components", ctx)
    owners = items["ownership:api"]  # a reason only one component has keeps its own item
    assert (owners["basis"], owners["score"]) == ("unresolved", -1)
    assert "(teams: ghosts)" in owners["reason"]
    assert "owner activity unknown: 1" in items["components"]["reason"]
    assert all("nothing to note" not in d for d in items["components"]["details"])


async def test_components_sharing_unknown_owners_fold_into_one_row(ctx):
    (ctx.repo_root / ".github").mkdir()
    (ctx.repo_root / ".github" / "CODEOWNERS").write_text("* @org/ghosts\n", encoding="utf-8")
    items = await _section("components", ctx)
    row = items["ownership:unknown"]
    assert (row["basis"], row["data"]) == ("unresolved", {"components": ["api", "web"]})
    assert row["details"] == ["2 component(s): api, web"]
    assert not any(n.startswith("ownership:") and n != "ownership:unknown" for n in items)
    assert "owner activity unknown: 2" in items["components"]["reason"]


async def test_hotspots_counted_per_component(ctx):
    items = await _section("hotspots", ctx)
    assert items["hotspots"]["data"]["count"] == 1
    assert items["hotspots:api"]["details"] == ["api/a.py"]
    assert "hotspots:web" not in items


async def test_bus_factor_is_per_component_and_names_nobody(ctx):
    items = await _section("bus-factor", ctx)
    # No catalog marks either component, so both are counted and named in the summary.
    assert set(items) == {"bus-factor"}
    summary = items["bus-factor"]
    assert summary["reason"] == "2 component(s); bus factor 1: 1, bus factor 2: 1"
    assert summary["details"] == ["bus factor 1: web", "bus factor 2: api"]
    api = summary["data"]["components"]["api"]
    assert (api["value"], api["basis"]) == (2, "inferred")  # under five owned files
    text = json.dumps(items)
    assert "Alice" not in text and "Bob" not in text and "example.com" not in text


async def test_bus_factor_item_for_a_component_the_catalog_marks(ctx):
    (ctx.repo_root / ".repowise" / "components.yaml").write_text(
        "apiVersion: backstage.io/v1alpha1\nkind: Component\nmetadata:\n  name: api\n"
        "  annotations:\n    repowise.dev/paths: api/**\n    repowise.dev/criticality: critical\n"
        "spec: {type: service, lifecycle: production, owner: group:default/core}\n",
        encoding="utf-8",
    )
    fresh = ReportContext(ctx.session, ctx.repository_id, ctx.repo_root)
    items = await _section("bus-factor", fresh)
    assert set(items) == {"bus-factor", "bus-factor:api"}
    assert "criticality: critical" in items["bus-factor:api"]["details"]
    assert items["bus-factor"]["details"] == ["bus factor 1: web"]


async def test_dead_code_tiers_and_gated_findings(ctx, monkeypatch):
    # No shipped row is gated: run on the audit's gated table.
    from repowise.core.analysis import finding_registry
    from tests.unit.server.mcp.test_language_gates import AUDIT_GATED

    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", AUDIT_GATED)
    items = await _section("dead-code", ctx)
    assert items["dead-code:high"]["data"]["count"] == 1
    assert items["dead-code:medium"]["data"]["count"] == 0
    gated = items["dead-code:gated:java"]
    assert (gated["basis"], gated["score"], gated["data"]["count"]) == ("gated", -1, 1)
    assert "safe" not in json.dumps(items).lower()


async def test_dead_code_not_run_is_inconclusive(ctx):
    from sqlalchemy import delete

    from repowise.core.persistence.models import DeadCodeFinding

    await ctx.session.execute(delete(DeadCodeFinding))
    items = await _section("dead-code", ctx)
    assert items["dead-code"]["basis"] == "unresolved"


async def test_test_reach_keeps_measured_and_inferred_apart(ctx):
    items = await _section("test-reach", ctx)
    assert (items["test-reach"]["basis"], items["test-reach"]["score"]) == ("inferred", 3.3)
    assert (items["line-coverage"]["basis"], items["line-coverage"]["score"]) == ("measured", 8.0)
    per = items["test-reach:components"]
    assert per["data"]["components"]["api"]["share"] == 0.5
    assert per["details"] == ["nothing to note: api, web"]
    assert "test-reach:api" not in items


async def test_unavailable_test_reach_is_said_once(ctx):
    from sqlalchemy import delete

    from repowise.core.persistence.models import HealthFileMetric

    await ctx.session.execute(delete(HealthFileMetric))
    items = await _section("test-reach", ctx)
    assert items["test-reach"]["basis"] == "unresolved"
    assert "line-coverage" not in items
    assert items["test-reach:api"]["basis"] == "unresolved"  # never dropped


async def test_contracts_unavailable_outside_a_workspace_then_measured(ctx, monkeypatch):
    from repowise.core.events import snapshot

    items = await _section("contracts", ctx)
    assert items["contracts"]["basis"] == "unresolved"
    monkeypatch.setattr(snapshot, "provided_contracts", lambda *_a: {})
    none = (await _section("contracts", ctx))["contracts"]
    assert "zero may mean none were detected" in none["reason"]
    monkeypatch.setattr(
        snapshot, "provided_contracts", lambda *_a: {"api/a.py": {"http::GET::/x": 2}}
    )
    items = await _section("contracts", ctx)
    assert items["contracts:api"]["data"]["provided"] == {"http::GET::/x": 2}
    assert items["contracts"]["data"]["provided"] == 1


async def test_decisions_per_component_with_stale(ctx, monkeypatch):
    from repowise.core.events import snapshot

    async def governing(*_a):
        return {"api/a.py": [("d1", "Use JWT")], "api/b.py": [("d2", "One DB")]}, {"d2"}

    from repowise.core.persistence.models import DecisionRecord

    monkeypatch.setattr(snapshot, "governing_decisions", governing)
    empty = await _section("decisions", ctx)
    assert list(empty) == ["decisions"] and empty["decisions"]["basis"] == "unresolved"
    ctx.session.add(DecisionRecord(repository_id=ctx.repository_id, title="Use JWT"))
    await ctx.session.flush()
    items = await _section("decisions", ctx)
    assert items["decisions"]["data"] == {"governing": 2, "recorded": 1, "stale": ["d2"]}
    assert items["decisions:api"]["details"] == ["d1: Use JWT", "d2 (stale): One DB"]
    assert "decisions:web" not in items
    assert items["decisions:components"]["data"]["components"]["web"] == {
        "governing": [],
        "stale": [],
    }


async def test_outcomes_are_left_out_until_a_checklist_kept_them(ctx):
    import repowise.core.report.sections  # noqa: F401
    from repowise.core.persistence.crud import upsert_changes

    def change(cid: str, path: str, outcomes: dict | None) -> dict:
        items = [] if outcomes is None else [outcomes]
        return {
            "change_id": cid,
            "identity_kind": "pr",
            "identity_value": cid[3:],
            "shape": "squash",
            "title": f"feat: {cid}",
            "head_sha": cid[3:].rjust(40, "a"),
            "files_json": json.dumps([{"path": path, "added": 1, "deleted": 0}]),
            # A kept checklist as the store writes it; the section reads it as every reader does.
            "evidence_json": json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": "2026-10-14T00:00:00+00:00",
                    "items": [
                        {"label": "Outcomes since merge", "basis": "declared", "source": "git history", **i}
                        for i in items
                    ],
                }
            )
            if outcomes is not None
            else None,
        }

    assert await report_sections.get("outcomes").collect(ctx) is None
    revert = {"kind": "revert", "sha": "r" * 40, "evidence": "This reverts commit x", "in_force": True}
    cause = {"kind": "fix_reference", "sha": "f" * 40, "evidence": "regression from #2"}
    item = lambda *links: {"kind": "outcomes", "status": "ok", "details": list(links)}  # noqa: E731
    unknown = {"kind": "outcomes", "status": "unknown", "details": [{"reason": "shallow"}]}
    await upsert_changes(
        ctx.session,
        ctx.repository_id,
        [
            change("pr-1", "api/a.py", item(revert)),
            change("pr-2", "web/W.java", item(cause)),
            change("pr-3", "api/b.py", item()),
            change("pr-4", "api/b.py", unknown),
            change("pr-5", "api/a.py", None),
        ],
    )
    await ctx.session.flush()
    items = await _section("outcomes", ctx)
    top = items["outcomes"]
    assert (top["basis"], top["score"]) == ("declared", None)
    assert top["data"]["read"] == 3 and top["data"]["changes"] == 5
    assert sorted(top["data"]["flagged"]) == ["pr-1", "pr-2"]
    assert "reverted: pr-1 feat: pr-1" in top["details"]
    assert any("regression from #2" in d for d in top["details"])
    comps = items["outcomes:components"]
    assert comps["data"]["components"]["api"] == {"read": ["pr-1", "pr-3"], "flagged": ["pr-1"]}
    assert set(items) == {"outcomes", "outcomes:components"}
    assert "caused" not in json.dumps(items)


async def test_provenance_names_agents_without_counting_them(ctx):
    items = await _section("provenance", ctx)
    cov = items["provenance:coverage"]
    assert cov["data"]["known"] == 3 and cov["data"]["commits"] == 4
    assert (cov["basis"], cov["score"]) == ("measured", None)  # coverage is not a grade
    assert items["provenance:agents"]["details"] == ["claude", "codex"]
    assert items["provenance:models"]["details"] == ["anthropic/claude-x"]
    text = json.dumps(items)
    assert "share" not in text and "human" not in text


async def test_provenance_without_markers_is_unresolved(ctx):
    from sqlalchemy import update

    from repowise.core.persistence.models import GitCommit

    await ctx.session.execute(update(GitCommit).values(attribution="unknown", agent_name=None))
    cov = (await _section("provenance", ctx))["provenance:coverage"]
    assert (cov["basis"], cov["score"]) == ("unresolved", -1)


async def test_provenance_shows_nothing_when_attribution_is_off(ctx):
    (ctx.repo_root / ".repowise" / "config.yaml").write_text(
        "attribution:\n  enabled: false\n", encoding="utf-8"
    )
    items = await _section("provenance", ctx)
    assert list(items) == ["provenance"] and items["provenance"]["basis"] == "unresolved"


async def test_trend_needs_a_same_stamp_snapshot_a_quarter_older(ctx):
    from repowise.core.persistence.models import ComponentSnapshot, SnapshotRun

    async def run(days_ago: int, stamp: str, hotspots: list[str]) -> None:
        r = SnapshotRun(
            repository_id=ctx.repository_id, taken_at=_NOW - timedelta(days=days_ago), stamp=stamp
        )
        ctx.session.add(r)
        await ctx.session.flush()
        facts = {"definition": "d", "hotspots": hotspots}
        ctx.session.add(ComponentSnapshot(run_id=r.id, component="api", facts_json=json.dumps(facts)))
        await ctx.session.flush()

    await run(0, "s1", ["a.py", "b.py"])
    await run(100, "s0", ["a.py"])
    assert (await _section("trend", ctx))["trend"]["basis"] == "unresolved"
    await run(120, "s1", ["a.py"])
    items = await _section("trend", ctx)
    assert items["trend"]["basis"] == "measured"
    # new_hotspot is off by default in the events config, so it does not count here.
    assert "trend:new_hotspot" not in items
    (ctx.repo_root / ".repowise" / "config.yaml").write_text(
        "events:\n  kinds: {new_hotspot: true}\n", encoding="utf-8"
    )
    items = await _section("trend", ctx)
    assert items["trend:new_hotspot"]["details"] == ["api: b.py"]


def test_long_detail_lists_are_capped():
    from repowise.core.report.model import capped

    assert capped([str(i) for i in range(25)], 20)[-1] == "and 5 more"
    assert capped(["a"], 20) == ["a"]


# ---------------------------------------------------------------------------
# Seam: a third-party section
# ---------------------------------------------------------------------------


async def test_a_plugin_section_registers_and_appears(ctx, tmp_path, monkeypatch):
    from repowise.core.registry import plugins
    from repowise.core.report.build import assemble, available_sections, collect_repo

    (tmp_path / "rw_report_plugin.py").write_text(
        "from repowise.core.report import Section, check, report_sections\n"
        "async def collect(ctx):\n"
        "    return [check('licence', reason='MIT', basis='declared', method='LICENSE file')]\n"
        "report_sections.register('licence', Section('Licence', 'The declared licence.', collect, 5))\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("REPOWISE_PLUGINS", "rw_report_plugin")
    plugins.reset()
    try:
        sections = available_sections()
        assert sections[0][0] == "licence"
        doc = assemble({"kind": "repo", "name": "shop"}, [await collect_repo(ctx, sections[:1])])
        (section,) = doc["sections"]
        assert section["checks"][0]["documentation"]["short"] == "The declared licence."
    finally:
        report_sections._impls.pop("licence", None)
        sys.modules.pop("rw_report_plugin", None)
        plugins.reset()


async def test_a_failing_section_is_inconclusive_not_fatal(ctx):
    from repowise.core.report.build import collect_repo

    async def boom(_ctx):
        raise RuntimeError("nope")

    async def leak(ctx_):
        raise OSError(f"cannot open {ctx_.repo_root / 'x.yaml'}")

    _entry, sections = await collect_repo(
        ctx, [("broken", Section("Broken", "b", boom)), ("leak", Section("Leak", "l", leak))]
    )
    assert sections[0]["checks"][0]["score"] == -1 and sections[0]["failed"] is True
    assert sections[0]["checks"][0]["details"] == ["nope"]
    assert sections[1]["checks"][0]["details"] == ["cannot open x.yaml"]


def test_scrub_removes_local_paths_and_keeps_contract_ids(tmp_path):
    from repowise.core.report.build import scrub

    root = tmp_path / "shop"
    out = scrub(
        {
            "a": f"cannot read: {root}/.repowise/components.yaml",
            "b": ["at /home/someone/x.py", r"in C:\Users\me\y.py", "http::GET::/users/{id}"],
            "c": str(root),
        },
        root,
    )
    assert out == {
        "a": "cannot read: .repowise/components.yaml",
        "b": ["at <path>", "in <path>", "http::GET::/users/{id}"],
        "c": ".",
    }


async def test_a_catalog_read_error_does_not_leak_the_repo_path(ctx, monkeypatch):
    from repowise.core.report.build import collect_repo

    catalog = ctx.repo_root / ".repowise" / "components.yaml"
    catalog.write_text("kind: Component\n", encoding="utf-8")
    real = Path.read_text

    def unreadable(self, *a, **k):  # the OSError quotes the absolute path, as a real one does
        if self == catalog:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", unreadable)
    _entry, sections = await collect_repo(ctx, [("components", report_sections.get("components"))])
    details = [d for s in sections for c in s["checks"] for d in c["details"]]
    assert any("cannot read" in d for d in details)
    for d in details:
        assert str(ctx.repo_root) not in d and ctx.repo_root.as_posix() not in d
        assert ":\\" not in d


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _doc() -> dict:
    from repowise.core.report.build import assemble

    items = [
        check("a|b", reason="<b>bold</b>", basis="measured", method="m", score=7.3),
        check("c", reason="none", basis="unresolved", method="m", details=["one"]),
    ]
    section = {"id": "s", "title": "S", "summary": "sum", "repo": None, "checks": items}
    entry = {"alias": None, "name": "shop", "head_commit": "c" * 40, "last_snapshot": None}
    return assemble({"kind": "repo", "name": "shop"}, [(entry, [section])], generated_at=_NOW)


def test_json_is_the_canonical_document():
    from repowise.core.report.render import report_renderers

    doc = _doc()
    assert json.loads(report_renderers.get("json")(doc)) == doc
    assert doc["schema_version"] == 1 and doc["scope"] == {"kind": "repo", "name": "shop"}


def test_markdown_is_stable_and_escapes_table_cells():
    from repowise.core.report.render import render_markdown

    text = render_markdown(_doc())
    assert text == render_markdown(_doc())
    assert r"| `a\|b` | 7.3 | measured | <b>bold</b> |" in text
    assert "| `c` | ? | unresolved | none |" in text
    assert "  - one" in text


def test_markdown_keeps_one_line_and_closes_code_spans():
    from repowise.core.report.render import render_markdown

    doc = _doc()
    item = doc["sections"][0]["checks"][1]
    item["name"], item["details"] = "c`d", ["two\nlines `x`"]
    doc["sections"][0]["summary"] = "multi\nline"
    text = render_markdown(doc)
    assert "  - two lines \\`x\\`" in text
    assert "- `c'd`" in text and "\nmulti line\n" in text


def test_html_is_self_contained_and_escaped():
    from repowise.core.report.render import render_html

    page = render_html(_doc())
    assert page.startswith("<!doctype html>")
    assert "default-src 'none'" in page
    for forbidden in ("<script", "<link", "src=", "http://", "https://", "@import"):
        assert forbidden not in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page and "<b>bold</b>" not in page
    assert "@media print" in page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from repowise.cli.helpers import run_async

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = (tmp_path / "shop").resolve()
    run_async(_seed(root))
    return root


def test_cli_writes_json_and_filters_sections(indexed: Path, tmp_path: Path) -> None:
    from repowise.cli.main import cli

    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        cli,
        ["report", "assessment", str(indexed), "--no-workspace", "--sections",
         "hotspots,dead-code", "--format", "json", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [s["id"] for s in doc["sections"]] == ["hotspots", "dead-code"]
    assert doc["repos"][0]["name"] == "shop"
    assert str(indexed) not in out.read_text(encoding="utf-8")


def test_cli_prints_markdown_and_rejects_unknown_names(indexed: Path) -> None:
    from repowise.cli.main import cli

    ok = CliRunner().invoke(cli, ["report", "assessment", str(indexed), "--no-workspace"])
    assert ok.exit_code == 0, ok.output
    assert ok.output.startswith("# Assessment: shop")
    bad = CliRunner().invoke(cli, ["report", "assessment", str(indexed), "--sections", "nope"])
    assert bad.exit_code == 2 and "available:" in bad.output
    fmt = CliRunner().invoke(cli, ["report", "assessment", str(indexed), "--format", "pdf"])
    assert fmt.exit_code == 2


def test_cli_without_an_index_says_so(tmp_path: Path) -> None:
    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["report", "assessment", str(tmp_path), "--no-workspace"])
    assert result.exit_code == 1 and "repowise init" in result.output


def test_cli_workspace_scope_covers_every_repo(tmp_path: Path, monkeypatch) -> None:
    from repowise.cli.helpers import run_async
    from repowise.cli.main import cli

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    ws = (tmp_path / "ws").resolve()
    run_async(_seed(ws / "shop"))
    (ws / "docs").mkdir()
    (ws / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos:\n  - path: shop\n    alias: shop\n    is_primary: true\n"
        "  - path: docs\n    alias: docs\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli, ["report", "assessment", str(ws), "--sections", "hotspots", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output[result.output.index("{") :])
    assert doc["scope"] == {"kind": "workspace", "name": "ws"}
    assert [(r["alias"], r.get("indexed", True)) for r in doc["repos"]] == [
        ("shop", True),
        ("docs", False),
    ]
    assert [s["repo"] for s in doc["sections"]] == ["shop"]


def test_cli_warns_when_a_section_failed(indexed: Path) -> None:
    import repowise.core.report.sections  # noqa: F401
    from repowise.cli.main import cli

    async def boom(_ctx):
        raise RuntimeError("nope")

    report_sections.register("zz-broken", Section("Broken", "b", boom, 999))
    try:
        result = CliRunner().invoke(
            cli, ["report", "assessment", str(indexed), "--sections", "zz-broken"]
        )
    finally:
        report_sections._impls.pop("zz-broken", None)
    assert result.exit_code == 0, result.output
    assert "1 section(s) failed" in result.output and "zz-broken" in result.output


def test_generated_at_is_whole_seconds():
    from repowise.core.report.build import assemble

    doc = assemble({"kind": "repo", "name": "x"}, [], generated_at=_NOW.replace(microsecond=123456))
    assert doc["generated_at"] == "2026-10-01T00:00:00+00:00"


async def test_component_hotspots_list_the_top_files_then_count_the_rest(ctx):
    from sqlalchemy import update

    from repowise.core.persistence.models import GitMetadata

    await ctx.session.execute(update(GitMetadata).values(is_hotspot=True))
    found = await ctx.rollups()
    found["api"]["hotspots"]["top"] = found["api"]["hotspots"]["top"][:1]  # as if capped at 1
    items = await _section("hotspots", ctx)
    assert items["hotspots:api"]["details"][-1] == "and 1 more"
