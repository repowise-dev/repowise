"""What ``decision add/list/show/health`` and a multi-id run print.

``test_decision_cmd.py`` pins the lifecycle; this file pins each report's
fields, in the table a person reads and the json an agent parses.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

from .test_decision_cmd import _seed_wiki_db


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """``add`` embeds the new record; a keyless embedder keeps that local."""
    import repowise.cli.providers.embedders as embedders
    from repowise.core.providers.embedding.base import KeylessEmbedder

    monkeypatch.setattr(embedders, "resolve_embedder_for_repo", lambda p: "keyless")
    monkeypatch.setattr(embedders, "build_embedder", lambda name, p: KeylessEmbedder())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    return root


_SEED = [
    {"id": "aaaaaaaa11111111", "title": "Active one", "status": "active", "staleness": 0.7},
    {
        "id": "bbbbbbbb22222222",
        "title": "Proposed one",
        "status": "proposed",
        "confidence": 0.4,
        "source": "git_archaeology",
    },
    {"id": "cccccccc33333333", "title": "Deprecated", "status": "deprecated"},
    {
        "id": "dddddddd44444444",
        "title": "Candidate",
        "status": "proposed",
        "affected_files": [],
        "rationale": "",
    },
]


@pytest.fixture
def seeded(repo: Path) -> Path:
    _seed_wiki_db(repo, _SEED)
    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    conn.execute(
        "UPDATE decision_records SET alternatives_json = ?, consequences_json = ?, "
        "tags_json = ?, evidence_line = ?, affected_files_json = ? WHERE id = ?",
        (
            json.dumps(["alt1", "alt2"]),
            json.dumps(["c1"]),
            json.dumps(["t1", "t2"]),
            42,
            json.dumps([f"f{i}.py" for i in range(12)]),
            "aaaaaaaa11111111",
        ),
    )
    conn.commit()
    conn.close()
    return repo


def _run(*args: str, input: str | None = None):
    # Keep stderr out of stdout on click 8.1 (8.2 always separates them).
    import inspect

    split = "mix_stderr" in inspect.signature(CliRunner.__init__).parameters
    runner = CliRunner(mix_stderr=False) if split else CliRunner()
    return runner.invoke(cli, ["decision", *args], input=input)


def _json(*args: str) -> dict:
    result = _run(*args, "--format", "json")
    return json.loads(result.stdout)


# -- show ---------------------------------------------------------------------


def test_show_panel_carries_every_filled_field(seeded: Path) -> None:
    result = _run("show", "aaaaaaaa", str(seeded))
    assert result.exit_code == 0, result.output
    out = result.output
    for line in (
        "Active one",
        "Status: active  |  Source: cli  |  Confidence: 90%",
        "Staleness: 0.70",
        "Context: forced by Z",
        "Decision: use X",
        "Rationale: because Y",
        "Alternatives rejected:",
        "  - alt1",
        "Consequences:",
        "  - c1",
        "Tags: t1, t2",
        "Evidence: aaaaaaaa11111111:42",
    ):
        assert line in out, line
    # The panel clips scope to ten entries to stay readable.
    assert "f9.py" in out
    assert "f10.py" not in out


def test_show_panel_omits_empty_sections(seeded: Path) -> None:
    out = _run("show", "dddddddd", str(seeded)).output
    assert "Rationale:" not in out
    assert "Alternatives rejected:" not in out
    assert "Affected files:" not in out
    assert "Evidence: dddddddd44444444" in out


def test_show_json_is_the_whole_record(seeded: Path) -> None:
    payload = _json("show", "aaaaaaaa", str(seeded))
    assert payload["query"] == "aaaaaaaa"
    record = payload["decision"]
    assert record["id"] == "aaaaaaaa11111111"
    assert record["alternatives"] == ["alt1", "alt2"]
    assert record["consequences"] == ["c1"]
    assert record["tags"] == ["t1", "t2"]
    assert len(record["affected_files"]) == 12, "json is never clipped"
    assert record["evidence_line"] == 42
    assert record["signature"] is None


def test_show_unknown_id_exits_nonzero_in_both_formats(seeded: Path) -> None:
    table = _run("show", "9999", str(seeded))
    assert table.exit_code == 1
    assert "Decision not found: 9999" in table.stdout
    as_json = _run("show", "9999", str(seeded), "--format", "json")
    assert as_json.exit_code == 1
    assert json.loads(as_json.stdout) == {"query": "9999", "decision": None}


# -- list ---------------------------------------------------------------------


def test_list_json_carries_full_ids(seeded: Path) -> None:
    payload = _json("list", str(seeded))
    assert payload["repo"] == str(seeded)
    by_id = {d["id"]: d for d in payload["decisions"]}
    assert set(by_id) == {d["id"] for d in _SEED}
    assert by_id["bbbbbbbb22222222"]["source"] == "git_archaeology"
    assert by_id["aaaaaaaa11111111"]["staleness_score"] == 0.7


def test_list_filters(seeded: Path) -> None:
    def ids(*flags: str) -> set[str]:
        return {d["id"][:8] for d in _json("list", str(seeded), *flags)["decisions"]}

    assert ids("--proposed") == {"bbbbbbbb", "dddddddd"}
    assert ids("--stale-only") == {"aaaaaaaa"}
    assert ids("--source", "git_archaeology") == {"bbbbbbbb"}
    assert ids("--status", "deprecated") == {"cccccccc"}


def test_list_table_rows(seeded: Path) -> None:
    out = _run("list", str(seeded)).output
    assert "Architectural Decisions" in out
    assert "aaaaaaaa" in out and "Active one" in out
    assert "40%" in out
    assert "0.7" in out


# -- health -------------------------------------------------------------------


@pytest.fixture
def accepted(seeded: Path) -> Path:
    """Health counts a decision as active only once it has been accepted."""
    assert _run("confirm", "aaaaaaaa", str(seeded)).exit_code == 0
    return seeded


def test_health_json_carries_whole_lists(accepted: Path) -> None:
    payload = _json("health", str(accepted))
    assert payload["repo"] == str(accepted)
    assert payload["summary"]["active"] == 1
    assert [d["id"] for d in payload["stale_decisions"]] == ["aaaaaaaa11111111"]
    proposed = {d["id"]: d["source"] for d in payload["proposed_awaiting_review"]}
    assert proposed["bbbbbbbb22222222"] == "git_archaeology"
    assert isinstance(payload["ungoverned_hotspots"], list)


def test_health_table_sections(accepted: Path) -> None:
    out = _run("health", str(accepted)).output
    assert "Decision Health" in out
    assert "Stale decisions (1):" in out
    assert "aaaaaaaa  Active one  (staleness: 0.70)" in out
    assert "Proposed decisions (2):" in out
    assert "(source: git_archaeology)" in out


def test_health_on_an_empty_store(repo: Path) -> None:
    result = _run("health", str(repo))
    assert result.exit_code == 0, result.output
    assert "Stale decisions (" not in result.output
    assert "Proposed decisions (" not in result.output


# -- add ----------------------------------------------------------------------


def test_add_json_echoes_the_full_id(repo: Path) -> None:
    payload = _json("add", str(repo), "--title", "T", "--decision", "D", "--kind", "agreement")
    assert payload["repo"] == str(repo)
    record = payload["decision"]
    assert len(record["id"]) == 32
    assert record == {**record, "title": "T", "status": "proposed", "kind": "agreement"}


def test_add_json_without_flags_refuses_rather_than_prompting(repo: Path) -> None:
    result = _run("add", str(repo), "--format", "json")
    assert result.exit_code == 1
    assert "--title and --decision are both required" in json.loads(result.stdout)["error"]


def test_add_prompts_split_comma_lists(repo: Path) -> None:
    answers = "architectural\nPrompted\nctx\nChose\nwhy\na, b\nc1\nsrc/x.py, src/y.py\nauth, api\n"
    result = _run("add", str(repo), input=answers)
    assert result.exit_code == 0, result.output
    assert "Decision recorded (active)" in result.output
    (decision,) = _json("list", str(repo))["decisions"]
    shown = _json("show", decision["id"], str(repo))["decision"]
    assert shown["alternatives"] == ["a", "b"]
    assert shown["consequences"] == ["c1"]
    assert shown["affected_files"] == ["src/x.py", "src/y.py"]
    assert shown["tags"] == ["auth", "api"]
    assert shown["context"] == "ctx"


def test_add_prompts_without_files_says_how_to_finish(repo: Path) -> None:
    answers = "architectural\nNo files\n\nChose\n\n\n\n\n\n"
    result = _run("add", str(repo), input=answers)
    assert result.exit_code == 0, result.output
    assert "Stored as a candidate" in result.output
    assert "--scope <path>" in result.output


# -- multi-id runs and deprecate ------------------------------------------------


def test_batch_table_counts_and_names_each_outcome(seeded: Path) -> None:
    result = _run("confirm", "bbbbbbbb", "dddddddd", "9999", str(seeded))
    assert result.exit_code == 1
    out = result.output
    assert "Accepted 1 of 3" in out
    assert "Decision not found: 9999" in out
    assert "Supply the missing parts with --reason, --scope or --evidence." in out


def test_batch_json_summarises_the_run(seeded: Path) -> None:
    result = _run("confirm", "bbbbbbbb", "dddddddd", str(seeded), "--format", "json")
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["action"] == "accepted"
    assert payload["preview"] is False
    assert (payload["succeeded"], payload["failed"]) == (1, 1)
    assert payload["remedy"].startswith("Supply the missing parts")


def test_a_clean_preview_says_nothing_was_written(seeded: Path) -> None:
    result = _run("dismiss", "bbbbbbbb", str(seeded), "--preview")
    assert result.exit_code == 0, result.output
    assert "Would dismiss 1 of 1" in result.output
    assert "Nothing was written. Re-run without --preview." in result.output


def test_dismiss_can_be_cancelled_at_the_prompt(seeded: Path) -> None:
    result = _run("dismiss", "dddddddd", str(seeded), input="n\n")
    assert result.exit_code == 0
    assert "Cancelled." in result.output
    assert _json("show", "dddddddd", str(seeded))["decision"]["status"] == "proposed"


def test_deprecate_refuses_an_unknown_successor(seeded: Path) -> None:
    result = _run("deprecate", "aaaaaaaa", str(seeded), "--superseded-by", "9999", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "decision_not_found"
    assert _json("show", "aaaaaaaa", str(seeded))["decision"]["status"] == "active"


def test_deprecate_json_reports_the_transition(seeded: Path) -> None:
    payload = _json("deprecate", "bbbbbbbb", str(seeded))
    assert payload == {"id": "bbbbbbbb22222222", "status": "deprecated", "action": "deprecated"}
