"""`repowise changes list` and `show` against a real local index."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.changes_cmd import changes_group
from repowise.cli.helpers import run_async

HEAD = "c" * 40


async def _store(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_changes

    (repo_path / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            await upsert_changes(
                session,
                repo.id,
                [
                    {
                        "change_id": "pr-5",
                        "identity_kind": "pr",
                        "identity_value": "5",
                        "shape": "squash",
                        "forge": "github",
                        "forge_ref": "#5",
                        "title": "fix: crash (#5)",
                        "merged_at": datetime(2026, 3, 1, tzinfo=UTC),
                        "head_sha": HEAD,
                        "commit_shas_json": json.dumps([HEAD]),
                        "files_json": json.dumps([{"path": "a.py", "added": 1, "deleted": 2}]),
                        "files_changed": 1,
                        "lines_added": 1,
                        "lines_deleted": 2,
                        "category": "fix",
                        "metadata_source": "git",
                    }
                ],
            )
    finally:
        await engine.dispose()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    run_async(_store(root))
    return root


def test_list_and_show_formats(tmp_path):
    root = _repo(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(changes_group, ["list", "--path", str(root), "--format", "json"])
    assert listed.exit_code == 0, listed.output
    data = json.loads(listed.output)
    assert data["total"] == 1 and data["changes"][0]["id"] == "pr-5"
    assert "files" not in data["changes"][0]

    table = runner.invoke(changes_group, ["list", "--path", str(root)])
    assert table.exit_code == 0 and "#5" in table.output

    shown = json.loads(
        runner.invoke(changes_group, ["show", "pr-5", "--path", str(root), "--format", "json"]).output
    )
    assert shown["schema_version"] == 1 and shown["files"][0]["path"] == "a.py"

    statement = json.loads(
        runner.invoke(
            changes_group, ["show", HEAD[:8], "--path", str(root), "--format", "in-toto"]
        ).output
    )
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["subject"] == [{"name": HEAD, "digest": {"gitCommit": HEAD}}]
    assert statement["predicateType"] == "https://repowise.dev/ChangeRecord/v1"

    missing = runner.invoke(changes_group, ["show", "pr-9", "--path", str(root)])
    assert missing.exit_code == 1


def test_no_index_exits_non_zero(tmp_path):
    root = tmp_path / "bare"
    (root / ".git").mkdir(parents=True)
    result = CliRunner().invoke(changes_group, ["list", "--path", str(root)])
    assert result.exit_code == 1 and "No index" in result.output


def _fake_evidence(monkeypatch):
    """Stand in for the review: these tests are about the command, not the evidence."""
    from repowise.core.changes import collect
    from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem

    calls: list[str] = []

    async def _evidence(session, repo_id, root, row, *, refresh=False, key=None, catalog=None):
        calls.append(row.change_id)
        return ChangeEvidence(
            change_id=row.change_id,
            revspec=row.head_sha,
            generated_at="2026-10-14T00:00:00+00:00",
            items=[
                EvidenceItem("owners", "Declared owners", "attention", "declared", ["@core"], [], "CODEOWNERS"),
                EvidenceItem("risk_score", "Diff size rank", "ok", "measured", {"percentile": 40.0}, [], "change risk"),
            ],
        )

    async def _build(session, repo_id, root, row, **_k):
        return await _evidence(session, repo_id, root, row)

    monkeypatch.setattr(collect, "change_evidence", _evidence)
    monkeypatch.setattr(collect, "build_stored_evidence", _build)
    return calls


def test_show_carries_the_checklist_unless_asked_not_to(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    calls = _fake_evidence(monkeypatch)
    runner = CliRunner()

    shown = json.loads(
        runner.invoke(changes_group, ["show", "pr-5", "--path", str(root), "--format", "json"]).output
    )
    assert shown["evidence"]["schema_version"] == 1
    assert [i["kind"] for i in shown["evidence"]["items"]] == ["owners", "risk_score"]

    table = runner.invoke(changes_group, ["show", "pr-5", "--path", str(root)])
    assert table.exit_code == 0 and "Evidence checklist" in table.output and "attention" in table.output

    bare = json.loads(
        runner.invoke(
            changes_group, ["show", "pr-5", "--path", str(root), "--format", "json", "--no-evidence"]
        ).output
    )
    assert "evidence" not in bare and calls == ["pr-5", "pr-5"]


def test_export_windows_and_formats(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _fake_evidence(monkeypatch)
    runner = CliRunner(mix_stderr=False)

    inside = runner.invoke(
        changes_group,
        ["export", "--path", str(root), "--since", "2026-02-01", "--until", "2026-04-01"],
    )
    assert inside.exit_code == 0, inside.output
    records = [json.loads(line) for line in inside.stdout.splitlines()]
    assert "evidence 1/1: pr-5" in inside.stderr
    assert [c["id"] for c in records] == ["pr-5"]
    assert records[0]["evidence"]["items"][0]["kind"] == "owners"

    outside = runner.invoke(changes_group, ["export", "--path", str(root), "--since", "2026-03-02"])
    assert outside.exit_code == 0 and outside.stdout == ""

    csv_out = runner.invoke(changes_group, ["export", "--path", str(root), "--format", "csv"])
    lines = csv_out.stdout.strip().splitlines()
    assert lines[0] == "change_id,merged_at,title,kind,label,status,basis,value,source"
    assert lines[1].startswith("pr-5,2026-03-01") and ",owners,Declared owners,attention,declared," in lines[1]
    assert len(lines) == 3


async def _keep_checklist(repo_path: Path, status: str) -> None:
    from repowise.core.changes.collect import save_evidence
    from repowise.core.changes.evidence import ChangeEvidence, EvidenceItem
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path

    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            evidence = ChangeEvidence(
                change_id="pr-5",
                revspec=HEAD,
                generated_at="2026-10-14T00:00:00+00:00",
                items=[EvidenceItem("owners", "Declared owners", status, "declared", None, [], "test")],
            )
            await save_evidence(session, repo.id, "pr-5", evidence)
    finally:
        await engine.dispose()


def test_list_needs_attention_reads_kept_checklists_only(tmp_path):
    root = _repo(tmp_path)
    runner = CliRunner()
    args = ["list", "--path", str(root), "--format", "json", "--needs-attention"]

    # No checklist kept yet: nothing is listed, and nothing is built.
    assert json.loads(runner.invoke(changes_group, args).output)["total"] == 0
    run_async(_keep_checklist(root, "ok"))
    assert json.loads(runner.invoke(changes_group, args).output)["total"] == 0
    run_async(_keep_checklist(root, "attention"))
    flagged = json.loads(runner.invoke(changes_group, args).output)
    assert flagged["total"] == 1 and flagged["changes"][0]["id"] == "pr-5"
    features = json.loads(runner.invoke(changes_group, [*args, "--category", "feature"]).output)
    assert features["total"] == 0


def test_outcomes_read_in_their_words_and_never_as_a_cause():
    from repowise.cli.commands.changes_cmd import outcome_lines

    item = {
        "kind": "outcomes",
        "status": "attention",
        "details": [
            {"kind": "revert", "sha": "b" * 40, "change_id": "pr-9", "evidence": 'Revert "x"', "in_force": False},
            {"kind": "fix_reference", "sha": "c" * 40, "change_id": None, "evidence": "regression from #5"},
            {"kind": "file_fix_rate", "files": 4, "files_fixed": 1, "days": 30, "window_complete": False},
        ],
    }
    assert outcome_lines(item) == [
        f'reverted by pr-9 ({"b" * 12}) (no longer in force): Revert "x"',
        f'referenced as the cause by a later fix, {"c" * 12} (their words): "regression from #5"',
        "files later received fixes: 1 of 4 within 30 days (window still open)",
    ]
    rate_only = {**item, "details": item["details"][2:]}
    assert outcome_lines(rate_only)[0].startswith("no later commit in this history reverts")
    assert not any("caused" in line for line in outcome_lines(item) + outcome_lines(rate_only))


def test_csv_cells_that_open_as_formulas_are_quoted():
    import csv
    import io

    from repowise.cli.commands.changes_cmd import _csv_rows

    record = {
        "id": "pr-1",
        "merged_at": None,
        "title": '=HYPERLINK("http://x")',
        "evidence": {
            "items": [
                {
                    "kind": "outcomes",
                    "label": "@label",
                    "status": "ok",
                    "basis": "declared",
                    "value": -1,
                    "source": "+git",
                }
            ]
        },
    }
    (row,) = csv.reader(io.StringIO(_csv_rows(record)))
    assert row[2] == "'=HYPERLINK(\"http://x\")" and row[4] == "'@label"
    assert row[7] == "'-1" and row[8] == "'+git" and row[0] == "pr-1"
