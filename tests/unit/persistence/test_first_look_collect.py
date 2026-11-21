"""The first-look collector over a real (in-memory) store.

One small repository with a finding in every section, plus the cases each
section must leave out: a test file's secret, tooling code, a lower dead-code
tier, a cause that may not lead, a cycle through tooling, vendor folders.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import delete

from repowise.core.persistence.models import (
    DeadCodeFinding,
    DocDriftFinding,
    GitCommitFile,
    GraphNodeMembership,
    HealthFileMetric,
    PerformanceOpportunity,
    Repository,
    SecurityFinding,
)
from repowise.core.report.first_look.collect import collect_first_look
from repowise.core.report.first_look.model import dead_code_ref, item_id
from repowise.core.report.first_look.prompts import render_item_prompt, render_report_prompt
from repowise.core.report.first_look.staleness import staleness
from repowise.core.report.first_look.store import load_report, store_report
from tests.unit.persistence.helpers import insert_repo

SECRET = "Sup3rS3cretValue99"
NOW = datetime(2026, 11, 20, tzinfo=UTC)

_ORIGINS = {
    "src/settings.py": "production",
    "src/core.py": "production",
    "src/dead.py": "production",
    "src/orphan.py": "production",
    "src/maybe.py": "production",
    "src/repo.py": "production",
    "web/a.ts": "production",
    "web/b.ts": "production",
    "web/c.ts": "production",
    "scripts/tool.py": "tooling",
    "scripts/dead_tool.py": "tooling",
    "tests/test_x.py": "test",
    "lib/json.hpp": "vendored",
    "vendor/lib.js": "vendored",
}


def _perf(
    rid: str, oid: str, path: str, *, may_lead: bool = True, eligible: bool = True
) -> PerformanceOpportunity:
    return PerformanceOpportunity(
        repository_id=rid, opportunity_id=oid, status="open", execution_context="production",
        actionability_state="plan_ready", plan_state="available",
        fix_strategy="batch_or_prefetch_io", boundary_kind="db", biomarker_type="io_in_loop",
        file_path=path, intervention_symbol=f"{path}::load", affected_call_sites_total=2,
        affected_files_total=1, queue_eligible=eligible, queue_value=3,
        details_json=json.dumps(
            {
                "may_lead": may_lead,
                "fix_rationale": "One query per row.",
                "facets": {"actionability_confidence": "medium", "loop_magnitude": "grows_with_data"},
                "plan": {
                    "effort_bucket": "M",
                    "steps": [{"order": 1, "action": "Batch the query", "file_path": path, "line": 40}],
                },
            }
        ),
    )


async def _seed(session) -> str:
    repo = await insert_repo(session)
    rid = repo.id
    repo.head_commit = "c" * 40
    session.add_all(
        HealthFileMetric(repository_id=rid, file_path=p, code_origin=o, score=5.0 + i * 0.1,
                         nloc=100, is_test=o == "test")
        for i, (p, o) in enumerate(_ORIGINS.items())
    )
    session.add_all(
        [
            SecurityFinding(repository_id=rid, file_path="src/settings.py", kind="hardcoded_password",
                            severity="high", snippet=f'PASSWORD = "{SECRET}"', line_number=4,
                            commit_sha=""),
            SecurityFinding(repository_id=rid, file_path="scripts/tool.py", kind="hardcoded_password",
                            severity="high", snippet=f'DB_PASSWORD = "{SECRET}x"', line_number=9,
                            commit_sha=""),
            SecurityFinding(repository_id=rid, file_path="tests/test_x.py", kind="hardcoded_password",
                            severity="high", snippet=f'PASSWORD = "{SECRET}y"', line_number=2,
                            commit_sha=""),
            *(
                DeadCodeFinding(repository_id=rid, kind="unused_export", file_path=path,
                                symbol_name=name, lines=10, confidence=0.9, safe_to_delete=True)
                for path, name in (("src/dead.py", "f1"), ("src/dead.py", "f2"), ("src/dead.py", "f3"),
                                   ("scripts/dead_tool.py", "g"))
            ),
            DeadCodeFinding(repository_id=rid, kind="unreachable_file", file_path="src/orphan.py",
                            lines=40, confidence=0.9, safe_to_delete=False),
            DeadCodeFinding(repository_id=rid, kind="unreachable_file", file_path="src/maybe.py",
                            lines=40, confidence=0.6, safe_to_delete=False),
            _perf(rid, "perf1", "src/repo.py"),
            _perf(rid, "perf2", "scripts/tool.py"),
            _perf(rid, "perf3", "src/core.py", may_lead=False),
            _perf(rid, "perf4", "src/core.py", eligible=False),
            DocDriftFinding(repository_id=rid, file_path="docs/guide.md", kind="path", line_number=5,
                            target="src/old.py", raw="src/old.py", confidence=0.9),
            GitCommitFile(repository_id=rid, sha="old", file_path="src/old.py"),
            *(
                GraphNodeMembership(repository_id=rid, node_id=n, node_type="file", scc_id=scc,
                                    scc_size=2)
                for scc, members in ((1, ("web/a.ts", "web/b.ts")), (2, ("web/c.ts", "scripts/tool.py")))
                for n in members
            ),
        ]
    )
    await session.commit()
    return rid


def _by_section(report) -> dict[str, list]:
    return {s.key: list(s.items) for s in report.sections}


async def test_every_section_reads_the_rows_next_reads(async_session) -> None:
    rid = await _seed(async_session)
    report = await collect_first_look(async_session, rid, now=NOW)
    sections = _by_section(report)

    assert report.stamp.commit == "c" * 40
    assert report.stamp.generator == "template"
    assert report.unavailable == {}

    # One fix (rotate, read from the environment) covers both files: one item.
    (secret,) = sections["security"]
    assert [loc.path for loc in secret.locations] == ["scripts/tool.py", "src/settings.py"]
    assert secret.id == item_id("security", "credentials", "")
    assert secret.rules == ("hardcoded_password",)
    assert secret.title_facts["files"] == 2
    assert secret.severity == "act_now"
    assert [m.store for m in secret.members] == ["action", "action"]
    assert report.item(report.top[0]).section == "security"

    perf = sections["performance"]
    assert [(i.locations[0].path, i.locations[0].line) for i in perf] == [("src/repo.py", 40)]
    assert perf[0].prompt.renderer == "perf_opportunity"

    dead = {i.rule: i for i in sections["dead_code"]}
    assert dead["safe_to_delete"].locations_total == 3
    assert dead["safe_to_delete"].prompt.renderer == "first_look_item"
    assert dead["safe_to_delete"].locations[0].path == "src/dead.py"
    assert dead["unreachable_file"].locations[0].path == "src/orphan.py"
    assert [m.ref for m in dead["safe_to_delete"].members] == [
        dead_code_ref("unused_export", f"f{n}", "src/dead.py") for n in (1, 2, 3)
    ]
    assert perf[0].members[0].ref == "perf1"
    assert len(sections["dead_code"]) == 2
    facts = report.section("dead_code").facts
    assert facts["batch_action_id"].startswith("act_")

    assert [i.locations[0].path for i in sections["docs"]] == ["docs/guide.md"]

    hygiene = {i.rule: i for i in sections["hygiene"]}
    assert [loc.path for loc in hygiene["import_cycle"].locations] == ["web/a.ts", "web/b.ts"]
    assert [loc.path for loc in hygiene["vendored_in_tree"].locations] == ["lib/json.hpp"]
    assert len(sections["hygiene"]) == 2

    assert "ownership" not in sections
    assert "decisions" not in sections


async def test_history_only_leaves_out_a_secret_still_in_the_working_tree(async_session) -> None:
    rid = await _seed(async_session)
    async_session.add_all(
        [
            # The working-tree secret in src/settings.py, introduced at an
            # earlier line: already an item, not history-only.
            SecurityFinding(repository_id=rid, file_path="src/settings.py", kind="hardcoded_password",
                            severity="high", snippet=f'PASSWORD = "{SECRET}"', line_number=2,
                            commit_sha="a" * 40),
            SecurityFinding(repository_id=rid, file_path="src/gone.py", kind="hardcoded_password",
                            severity="high", snippet=f'PASSWORD = "{SECRET}z"', line_number=1,
                            commit_sha="b" * 40),
        ]
    )
    await async_session.commit()
    report = await collect_first_look(async_session, rid, now=NOW)
    assert report.section("security").facts["history_only"] == 1


async def test_no_item_or_prompt_holds_a_secret(async_session) -> None:
    rid = await _seed(async_session)
    report = await collect_first_look(async_session, rid, now=NOW)
    assert SECRET not in json.dumps(report.to_json())
    for item in _by_section(report)["security"]:
        # A group's prompt covers every location; each member's action prompt
        # would name one file.
        assert item.prompt.renderer == "first_look_item"
        prompt = await render_item_prompt(async_session, rid, item, flavor="claude-code")
        assert all(loc.path in prompt for loc in item.locations)
        assert SECRET not in prompt


async def test_non_production_code_is_left_out_outside_hygiene(async_session) -> None:
    rid = await _seed(async_session)
    report = await collect_first_look(async_session, rid, now=NOW)
    for section in report.sections:
        if section.key in ("security", "docs", "hygiene"):
            continue
        assert {i.origin for i in section.items} <= {"production"}, section.key


async def test_prompts_resolve_through_their_renderers(async_session) -> None:
    rid = await _seed(async_session)
    report = await collect_first_look(async_session, rid, now=NOW)
    sections = _by_section(report)
    perf = await render_item_prompt(async_session, rid, sections["performance"][0])
    assert "Batch the query" in perf and "src/repo.py:40" in perf
    cycle = next(i for i in sections["hygiene"] if i.rule == "import_cycle")
    assert "web/b.ts" in await render_item_prompt(async_session, rid, cycle)
    whole = render_report_prompt(report)
    assert all(i.id in whole for i in report.items())


async def test_the_stored_report_round_trips_and_goes_stale(async_session) -> None:
    rid = await _seed(async_session)
    report = await collect_first_look(async_session, rid, now=NOW)
    await store_report(async_session, rid, report)
    await async_session.commit()
    assert (await load_report(async_session, rid)).to_json() == report.to_json()

    await async_session.execute(
        delete(SecurityFinding).where(SecurityFinding.file_path == "src/settings.py")
    )
    (await async_session.get(Repository, rid)).head_commit = "d" * 40
    await async_session.commit()
    found = await staleness(async_session, rid, report)
    # The credentials group keeps its id with one file fewer: changed, not fixed.
    assert found.fixed == () and found.new == ()
    assert found.changed == (item_id("security", "credentials", ""),)
    assert found.behind

    await async_session.execute(delete(SecurityFinding))
    await async_session.commit()
    found = await staleness(async_session, rid, report)
    assert found.fixed == (item_id("security", "credentials", ""),)


async def test_deletions_below_the_batch_floor_are_not_listed(async_session) -> None:
    rid = await _seed(async_session)
    await async_session.execute(
        delete(DeadCodeFinding).where(DeadCodeFinding.symbol_name.in_(("f2", "f3")))
    )
    await async_session.commit()
    report = await collect_first_look(async_session, rid, now=NOW)
    rules = {i.rule for i in report.section("dead_code").items}
    assert rules == {"unreachable_file"}
