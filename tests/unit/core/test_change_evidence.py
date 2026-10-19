"""The evidence checklist on a Change Record: the builder, the gather and the cache."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core import attribution
from repowise.core.analysis.change_health.models import ChangeFinding, ChangeHealthDelta
from repowise.core.analysis.change_review import (
    LANES,
    ChangeManifestEntry,
    ChangeReviewBundle,
    ChangeReviewRequest,
    LaneState,
)
from repowise.core.analysis.review_directive import ReviewDirective
from repowise.core.changes.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    KINDS,
    ChangeEvidence,
    EvidenceInputs,
    EvidenceItem,
    Unavailable,
    build_change_evidence,
    item_line,
    line_text,
)


def _entry(path: str) -> ChangeManifestEntry:
    return ChangeManifestEntry(
        path=path, base_path=path, head_path=path, status="modified", diff_reliability="parsed"
    )


def _finding(kind: str = "introduced") -> ChangeFinding:
    return ChangeFinding(
        change_finding_id="cf1",
        change_kind=kind,
        dimension="maintainability",
        biomarker_type="long_function",
        severity="medium",
        path="src/a.py",
        symbol="run",
        line_start=1,
        line_end=40,
        reason="run grew to 40 lines",
        attribution_basis="changed_lines",
        attribution_confidence="high",
        attribution_detail="",
        suggestion="",
        follow_up="",
    )


def _tests(files: dict[str, str], *, map_current: bool = False) -> dict:
    """A test-impact result: per file ``inferred``, ``measured`` or ``unknown`` (none found)."""
    rows = []
    for p, s in files.items():
        stem = p.rsplit("/", 1)[-1]
        rows.append(
            {
                "source_file": p,
                "status": s,
                "measured_tests": [f"tests/test_{stem}::t"] if s == "measured" else [],
                "inferred_tests": [f"tests/test_{stem}"] if s == "inferred" else [],
                "inferred_tests_total": 1 if s == "inferred" else 0,
            }
        )
    return {
        "recommendations_by_primary_basis": {
            "measured": sum(s == "measured" for s in files.values()),
            "inferred": sum(s == "inferred" for s in files.values()),
        },
        "files": rows,
        "coverage": {
            "map_present": map_current,
            "freshness": {"status": "current" if map_current else "unknown"},
        },
    }


def _bundle(paths: list[str], *, health=None, tests=None, risk=None) -> ChangeReviewBundle:
    lanes = {name: LaneState("unsupported", "not gathered") for name in LANES}
    return ChangeReviewBundle(
        request=ChangeReviewRequest(revspec="abc"),
        base=None,
        head=None,
        manifest=tuple(_entry(p) for p in paths),
        directive=ReviewDirective(status="unknown", headline="x", evidence_state="unavailable"),
        lanes=lanes,
        risk=risk,
        health=health,
        tests=tests,
    )


def _delta(findings: list[ChangeFinding]) -> ChangeHealthDelta:
    return ChangeHealthDelta(
        status="available",
        explanation="",
        base=None,
        head=None,
        comparison_basis="commit",
        fingerprint=None,
        findings=findings,
    )


def test_every_kind_is_present_in_order_and_round_trips():
    evidence = build_change_evidence(_bundle(["src/a.py"]), change_id="pr-1", revspec="abc")
    assert [i.kind for i in evidence.items] == list(KINDS)
    data = evidence.to_dict()
    assert data["schema_version"] == EVIDENCE_SCHEMA_VERSION == 1
    assert set(data) >= {"schema_version", "change_id", "revspec", "generated_at", "items"}
    assert set(data["items"][0]) == {
        "kind",
        "label",
        "status",
        "basis",
        "value",
        "details",
        "source",
    }
    assert ChangeEvidence.from_dict(json.loads(json.dumps(data))).to_dict() == data
    # Nothing gathered: every item says so rather than reading as clear.
    assert all(i.status == "unknown" and i.basis == "unresolved" for i in evidence.items)


def test_statuses_follow_the_facts():
    paths = ["src/a.py", "src/b.py", "tests/test_a.py"]
    bundle = _bundle(
        paths,
        health=_delta([_finding()]),
        tests=_tests({"src/a.py": "inferred", "src/b.py": "unknown", "tests/test_a.py": "unknown"}),
        risk=SimpleNamespace(percentile=97.5, baseline_sample_size=200),
    )
    inputs = EvidenceInputs(
        code_paths=["src/a.py", "src/b.py"],
        components=[
            {
                "name": "core",
                "source": "declared",
                "owner": "team-core",
                "criticality": "high",
                "tags": ["pci"],
                "files": ["src/a.py"],
                "bus_factor": 1,
            }
        ],
        owners={"src/a.py": ["@team-core"]},
        owner_sources=["CODEOWNERS"],
        reviewers=[{"name": "Bob", "paths": ["src/a.py"], "reasons": ["owns path"]}],
        contracts=[],
        consumers=[{"repo": "web", "file": "x.ts", "contract_id": "http::GET::/a", "provider_file": "src/a.py"}],
        decisions=[{"id": "d1", "title": "Keep a pure", "authority": "accepted", "paths": ["src/a.py"]}],
        provenance={
            "label": "agent",
            "coverage": 1.0,
            "agent_commits": 1,
            "commits": [{"sha": "abc", "label": "agent", "tier": 2}],
        },
    )
    evidence = build_change_evidence(bundle, inputs, change_id="pr-1", revspec="abc")
    by = {i.kind: i for i in evidence.items}

    assert by["components_touched"].basis == "declared" and by["components_touched"].value == ["core"]
    # src/b.py is code with no declared owner; the test file does not count.
    assert by["owners"].status == "attention"
    assert by["owners"].details[-1] == {"owner": None, "paths": ["src/b.py"]}
    assert by["suggested_reviewers"].value == ["Bob"] and "score" not in by["suggested_reviewers"].details[0]
    tests = by["reaching_tests"]
    # One file reached, one nobody knows about: partly known, never a gap.
    assert tests.status == "unknown" and tests.basis == "inferred"
    assert tests.value == {
        "measured": 0,
        "inferred": 1,
        "files_without_tests": 0,
        "files_unknown": 1,
    }
    # No current measured map: an empty answer is "nobody knows", not absence.
    assert [(r["path"], r["tests"]) for r in tests.details] == [
        ("src/a.py", ["tests/test_a.py"]),
        ("src/b.py", None),
    ]
    assert by["cross_repo_consumers"].status == "attention"
    assert by["cross_repo_consumers"].value == {"repos": ["web"], "links": 1}
    assert by["decisions_crossed"].status == "attention"
    assert by["health_delta"].status == "attention" and by["health_delta"].value["introduced"] == 1
    assert by["provenance"].status == "ok" and by["provenance"].value["label"] == "agent"
    assert by["provenance"].details == [{"sha": "abc", "label": "agent", "tier": 2}]
    # A high diff-size rank is one row and never a verdict.
    assert by["risk_score"].status == "ok" and by["risk_score"].value == {"percentile": 97.5}


def test_a_kept_checklist_reads_reaching_tests_under_the_current_rule():
    """A checklist kept before the rule changed said attention for an unknown file."""
    item = {
        "kind": "reaching_tests",
        "label": "Tests reaching the change",
        "status": "attention",
        "basis": "inferred",
        "value": {"files_without_tests": 0, "files_unknown": 1},
        "details": [{"path": "a.py", "tests": ["t"]}, {"path": "b.py", "tests": None}],
        "source": "test impact",
    }
    assert EvidenceItem.from_dict(item).status == "unknown"
    absent = {**item, "details": [*item["details"], {"path": "c.py", "tests": []}]}
    assert EvidenceItem.from_dict(absent).status == "attention"

    # The needs-attention list confirms its SQL prefilter through the same parse.
    from repowise.core.changes.collect import evidence_summary
    from repowise.core.persistence.crud.changes import dump_evidence

    kept = {"schema_version": 1, "generated_at": "2026-10-14T00:00:00+00:00", "items": [item]}
    row = SimpleNamespace(evidence_json=dump_evidence(kept))
    assert evidence_summary(row)["attention"] == 0

    # Health leads the row from its own rollup; its checklist row is not counted.
    health = {**item, "kind": "health_delta", "label": "Health delta", "details": []}
    kept = {**kept, "items": [health]}
    row = SimpleNamespace(evidence_json=dump_evidence(kept))
    assert evidence_summary(row) | {"generated_at": None} == {
        "generated_at": None,
        "ok": 0,
        "attention": 0,
        "unknown": 0,
    }


def test_every_item_reads_as_a_plain_line():
    """Each kind words its value; no raw key, boolean or dict reaches a reader."""
    bundle = _bundle(
        ["src/a.py", "src/b.py"],
        health=_delta([_finding()]),
        tests=_tests({"src/a.py": "inferred", "src/b.py": "unknown"}),
        risk=SimpleNamespace(percentile=97.5, baseline_sample_size=200),
    )
    inputs = dataclasses.replace(_full_inputs(), code_paths=None)
    evidence = build_change_evidence(bundle, inputs, change_id="c", revspec="r").to_dict()
    lines = {i["kind"]: line_text(item_line(i)) for i in evidence["items"]}

    assert lines["reaching_tests"] == "1 of 2 changed files reached by a test; 1 not known"
    assert lines["components_touched"] == "1 component: core"
    assert lines["owners"] == "@team-core; 1 changed file without one"
    assert lines["contracts"] == "1 workspace contract touched; signatures not compared"
    assert lines["decisions_crossed"] == "1 accepted decision, 0 candidates"
    assert lines["cross_repo_consumers"] == "No consumer in another repo"
    assert lines["risk_score"] == "Larger than 97.5% of recent commits"
    assert lines["outcomes"].startswith("Not known: ")
    assert lines["provenance"] == "provenance: agent, known for 0 of 0 commits"
    named = item_line(
        {
            "kind": "provenance",
            "value": {"label": "agent", "agent_commits": 1, "human_attested_commits": 0, "unknown_commits": 0},
            "details": [{"sha": "s", "label": "agent", "tier": 2, "agent": "claude-code", "model": "opus"}],
        }
    )
    assert line_text(named) == "provenance: agent, known for 1 of 1 commit (claude-code, opus)"
    for text in lines.values():
        assert text and not any(w in text for w in ("True", "False", "None", "_", "{"))


def test_unknown_provenance_stays_unknown_and_unavailable_inputs_name_why():
    inputs = EvidenceInputs(
        owners=Unavailable("could not be read: OSError"),
        provenance={"label": "unknown", "coverage": 0.0},
        decisions=[{"id": "d", "title": "t", "authority": "candidate", "paths": ["a.py"]}],
    )
    evidence = build_change_evidence(_bundle(["a.py"]), inputs, change_id=None, revspec=None)
    by = {i.kind: i for i in evidence.items}
    assert by["provenance"].status == "unknown" and by["provenance"].basis == "unresolved"
    assert by["owners"].details == [{"reason": "could not be read: OSError"}]
    # Only a candidate: listed, but nothing anyone has signed.
    assert by["decisions_crossed"].status == "ok" and by["decisions_crossed"].basis == "inferred"


def test_reaching_tests_absence_is_known_only_against_a_current_map():
    bundle = _bundle(
        ["src/a.py", "src/b.py", "src/c.py"],
        tests=_tests({"src/a.py": "measured", "src/b.py": "unknown"}, map_current=True),
    )
    evidence = build_change_evidence(bundle, change_id=None, revspec=None)
    tests = {i.kind: i for i in evidence.items}["reaching_tests"]
    by_path = {r["path"]: r["tests"] for r in tests.details}
    assert by_path == {"src/a.py": ["tests/test_a.py::t"], "src/b.py": [], "src/c.py": None}
    # c.py is missing from the result: unknown, so the item is neither measured nor ok.
    assert tests.status == "attention" and tests.basis == "inferred"


def _full_inputs() -> EvidenceInputs:
    return EvidenceInputs(
        code_paths=["src/a.py"],
        components=[
            {
                "name": "core",
                "source": "declared",
                "owner": "team-core",
                "criticality": "high",
                "tags": ["pci"],
                "files": ["src/a.py"],
                "bus_factor": None,
            }
        ],
        owners={"src/a.py": ["@team-core"]},
        owner_sources=["CODEOWNERS"],
        reviewers=[{"name": "Bob", "paths": ["src/a.py"], "reasons": ["co-change", "owns path"]}],
        contracts=[
            {"path": "src/a.py", "contract_id": "http::GET::/a", "type": "http", "role": "provider"}
        ],
        consumers=[],
        decisions=[
            {"id": "d1", "title": "t", "authority": "accepted", "status": "active", "paths": ["src/a.py"]}
        ],
        provenance={
            "label": "agent",
            "coverage": 1.0,
            "commits": [{"sha": "s1", "label": "agent", "tier": None}],
        },
    )


def test_detail_rows_keep_the_policy_contract():
    """The row keys and types the local change policy reads, kind by kind."""
    from repowise.core.analysis.change_contracts import ContractImpact, SymbolContractChange

    impact = ContractImpact(
        changes=[
            SymbolContractChange(
                file="src/a.py",
                name="run",
                symbol_id="src/a.py::run",
                kind="function",
                change="signature",
                start_line=1,
                end_line=3,
                outside_production_callers=["src/b.py::use"],
            )
        ]
    )
    base = _bundle(["src/a.py"], tests=_tests({"src/a.py": "inferred"}))
    bundle = ChangeReviewBundle(
        request=base.request,
        base=None,
        head=None,
        manifest=base.manifest,
        directive=base.directive,
        lanes={**base.lanes, "contracts": LaneState("available")},
        tests=base.tests,
        contracts=impact,
    )
    items = build_change_evidence(bundle, _full_inputs(), change_id="c", revspec="r").items
    by = {i.kind: i for i in items}

    for row in by["components_touched"].details:
        assert {"name", "criticality", "tags", "owner", "files", "bus_factor"} <= set(row)
        assert row["owner"] is None or isinstance(row["owner"], str)
        assert row["bus_factor"] is None or isinstance(row["bus_factor"], int)
        assert isinstance(row["tags"], list) and isinstance(row["files"], list)
    for row in by["reaching_tests"].details:
        assert isinstance(row["path"], str)
        assert row["tests"] is None or all(isinstance(t, str) for t in row["tests"])
    contracts = by["contracts"].details
    assert contracts and all(
        isinstance(r["path"], str) and isinstance(r["breaking"], bool) for r in contracts
    )
    assert [r["breaking"] for r in contracts] == [False, True]
    for row in by["provenance"].details:
        assert set(row) == {"sha", "label", "tier"}
        assert row["label"] in ("agent", "human_attested", "unknown")
        assert row["tier"] is None or row["tier"] in (1, 2, 3)
    assert isinstance(by["provenance"].value, dict) and by["provenance"].value["label"] == "agent"
    for row in by["decisions_crossed"].details:
        assert {"id", "title", "paths"} <= set(row) and isinstance(row["paths"], list)
    for row in by["suggested_reviewers"].details:
        assert set(row) == {"name", "paths", "reasons"}
        assert set(row["reasons"]) <= {"owns path", "recent change", "co-change"}


def test_the_policy_evaluator_reads_the_built_record():
    """A record from the builder drives ``repowise check`` rules end to end."""
    from repowise.core.policy import evaluate, parse_policy

    policy = parse_policy(
        """\
rules:
  - name: agent-pci-needs-owner
    match:
      components: {tags: [pci]}
    when:
      attribution: agent
      reaching_tests: missing
    action: require_owner_review
  - name: no-contract-break
    when:
      contract_break: true
    action: warn
  - name: decision
    when:
      decision_crossed: true
    action: warn
"""
    )
    inputs = _full_inputs()
    bundle = _bundle(
        ["src/a.py"], tests=_tests({"src/a.py": "unknown"}, map_current=True)
    )
    record = build_change_evidence(bundle, inputs, change_id="c", revspec="r").to_dict()
    results = {r.rule.name: r for r in evaluate(policy, json.loads(json.dumps(record)))}
    owner = results["agent-pci-needs-owner"]
    assert owner.outcome == "triggered" and owner.owners == ["team-core"]
    # The contract row is a touched contract, not a break.
    assert results["no-contract-break"].outcome == "passed"
    assert results["decision"].outcome == "triggered"


@pytest.mark.asyncio
async def test_an_older_store_gains_evidence_json_on_init_db(tmp_path):
    """A local SQLite index from before the column gets it from the startup reconcile."""
    import sqlite3

    from repowise.core.persistence import create_engine, init_db

    db = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE changes DROP COLUMN evidence_json")
        assert "evidence_json" not in {r[1] for r in conn.execute("PRAGMA table_info(changes)")}
    engine = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()
    with sqlite3.connect(db) as conn:
        assert "evidence_json" in {r[1] for r in conn.execute("PRAGMA table_info(changes)")}


# ---------------------------------------------------------------------------
# Gather and cache over a real repository and index
# ---------------------------------------------------------------------------


@pytest.fixture
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _git(root: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ann",
        "GIT_AUTHOR_EMAIL": "ann@example.com",
        "GIT_COMMITTER_NAME": "Ann",
        "GIT_COMMITTER_EMAIL": "ann@example.com",
    }
    return subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "CODEOWNERS").write_text("src/core/ @team-core\n", encoding="utf-8")
    (root / "src" / "core").mkdir(parents=True)
    (root / "src" / "core" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "chore: seed")
    (root / "src" / "core" / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (root / "src" / "web.py").write_text("def w():\n    return 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(
        root,
        "commit",
        "-q",
        "-m",
        "feat: a and web (#7)\n\nCo-authored-by: Carol <carol@example.com>",
    )
    return root, _git(root, "rev-parse", "HEAD")


async def _index(tmp_path: Path, root: Path, head: str):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_changes, upsert_decision
    from repowise.core.persistence.database import get_session

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(root))
        await upsert_changes(
            session,
            repo.id,
            [
                {
                    "change_id": "pr-7",
                    "identity_kind": "pr",
                    "identity_value": "7",
                    "shape": "squash",
                    "forge": "github",
                    "forge_ref": "#7",
                    "title": "feat: a and web (#7)",
                    "merged_at": datetime(2026, 3, 1, tzinfo=UTC),
                    "head_sha": head,
                    "author_name": "Ann",
                    "author_email": "ann@example.com",
                    "commit_shas_json": json.dumps([head]),
                    "files_json": "[]",
                    "category": "feature",
                    "provenance": "unknown",
                    "provenance_agent": 0,
                    "provenance_known": 0,
                    "metadata_source": "git",
                }
            ],
        )
        from repowise.core.persistence.crud import upsert_git_metadata

        authors = [
            {"name": n, "email": f"{n.lower()}@example.com", "commit_count": c}
            for n, c in (("Zed", 1), ("Ann", 9), ("Carol", 5), ("Bob", 3))
        ]
        await upsert_git_metadata(
            session,
            repository_id=repo.id,
            file_path="src/core/a.py",
            top_authors_json=json.dumps(authors),
            commit_count_90d=4,
        )
        await upsert_decision(
            session,
            repository_id=repo.id,
            title="Keep a pure",
            decision="a has no side effects",
            affected_files=["src/core/a.py"],
        )
        repo_id = repo.id
    return engine, factory, repo_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("_private_user_config")
async def test_stored_change_evidence_is_gathered_then_kept(tmp_path, repo):
    from repowise.core.changes.collect import change_evidence
    from repowise.core.persistence.crud import get_change
    from repowise.core.persistence.database import get_session

    root, head = repo
    engine, factory, repo_id = await _index(tmp_path, root, head)
    try:
        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-7")
            first = await change_evidence(session, repo_id, root, row)
        by = {i.kind: i for i in first.items}
        assert first.change_id == "pr-7" and first.revspec == head
        owners = by["owners"]
        assert owners.basis == "declared" and owners.value == ["@team-core"]
        assert owners.status == "attention"  # src/web.py has no declared owner
        assert {"owner": None, "paths": ["src/web.py"]} in owners.details
        decisions = by["decisions_crossed"]
        assert decisions.value == {"accepted": 0, "candidate": 1}
        assert decisions.details[0]["paths"] == ["src/core/a.py"]
        provenance = by["provenance"]
        assert provenance.value["label"] == "unknown" and provenance.basis == "unresolved"
        assert provenance.details == [{"sha": head, "label": "unknown", "tier": None}]
        assert by["health_delta"].basis == "measured"
        # Ann wrote the change and Carol co-wrote it; the rest are listed by name.
        reviewers = by["suggested_reviewers"]
        assert reviewers.value == ["Bob", "Zed"]
        assert reviewers.details[0] == {
            "name": "Bob",
            "paths": ["src/core/a.py"],
            "reasons": ["owns path"],
        }
        assert by["cross_repo_consumers"].basis == "unresolved"

        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-7")
            assert json.loads(row.evidence_json)["generated_at"] == first.generated_at
            again = await change_evidence(session, repo_id, root, row)
            assert again.to_dict() == first.to_dict()
            rebuilt = await change_evidence(session, repo_id, root, row, refresh=True)
            assert [i.kind for i in rebuilt.items] == list(KINDS)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_without_an_index_only_git_items_are_filled(repo):
    from repowise.core.changes.collect import evidence_for_revspec

    root, head = repo
    evidence = await evidence_for_revspec(None, "", root, head)
    by = {i.kind: i for i in evidence.items}
    assert by["owners"].details == [{"reason": "the repository has no index"}]
    assert by["health_delta"].status in {"ok", "attention"}
    assert evidence.index_commit is None
