"""Pi augment payloads: translation, dispatch, and the failure rescue.

The extension hands ``repowise-augment --client pi`` a Pi-shaped payload;
this module is the Python end of that contract, driven through the real
stdin/stdout entry point wherever the behaviour lives in the shared
dispatch rather than in the translation itself.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.commands.augment_cmd.pi import is_pi_tool, normalize_pi_payload
from repowise.cli.main import cli

_INDEXED = "1" * 40


def _invoke_augment(
    runner: CliRunner,
    payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    args: list[str] | None = None,
):
    # The dedup marker is keyed on (event, context); identical blocks
    # across fast test runs would suppress the emission, so bypass it.
    monkeypatch.setattr(
        "repowise.cli.commands.augment_cmd.command._claim_emission", lambda e, c: True
    )
    return runner.invoke(cli, ["augment", *(args or ["--client", "pi"])], input=json.dumps(payload))


def _hook_output(output: str) -> dict[str, Any]:
    return json.loads(output)["hookSpecificOutput"]


def _init_repowise_repo(path: Path) -> None:
    repowise_dir = path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "state.json").write_text(
        json.dumps({"last_sync_commit": _INDEXED, "docs_mode": "llm"}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def test_normalize_read_path_to_file_path() -> None:
    payload = normalize_pi_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "read",
            "tool_input": {"path": "src/app.py", "offset": 10, "limit": 20},
            "tool_response": {"content": [{"type": "text", "text": "x = 1\n"}]},
            "cwd": "/repo",
        }
    )
    assert payload["tool_input"]["file_path"] == "src/app.py"
    assert payload["tool_input"]["path"] == "src/app.py"
    assert payload["tool_input"]["offset"] == 10
    assert payload["tool_response"] == {
        "file": {"filePath": "src/app.py", "content": "x = 1\n"}
    }


def test_normalize_bash_exit_code_from_is_error() -> None:
    ok = normalize_pi_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"content": [{"type": "text", "text": "3 passed"}]},
            "cwd": "/repo",
        }
    )
    assert ok["tool_response"] == {"stdout": "3 passed", "exit_code": 0}
    failed = normalize_pi_payload(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"content": [{"type": "text", "text": "boom"}]},
            "cwd": "/repo",
            "is_error": True,
        }
    )
    assert failed["tool_response"] == {"stdout": "boom", "exit_code": 1}


def test_normalize_grep_has_no_stated_count() -> None:
    payload = normalize_pi_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "grep",
            "tool_input": {"pattern": "TODO"},
            "tool_response": {"content": [{"type": "text", "text": "No matches found"}]},
            "cwd": "/repo",
        }
    )
    # No numLines: the digest surface counts the text, which is the path
    # that honours the no-match sentinel. A stated count would read this
    # as a hit.
    assert payload["tool_response"] == {"mode": "content", "content": "No matches found"}


def test_normalize_find_becomes_glob_filenames() -> None:
    payload = normalize_pi_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "find",
            "tool_input": {"pattern": "**/*.py"},
            "tool_response": {"content": [{"type": "text", "text": "a.py\nb.py\n"}]},
            "cwd": "/repo",
        }
    )
    assert payload["tool_response"] == {"filenames": ["a.py", "b.py"], "numFiles": 2}


def test_normalize_unknown_tool_passes_through() -> None:
    payload = {"hook_event_name": "PostToolUse", "tool_name": "ls", "cwd": "/repo"}
    assert normalize_pi_payload(payload) == payload
    assert is_pi_tool("read") is True
    assert is_pi_tool("ls") is False
    assert is_pi_tool(None) is False


def test_normalize_malformed_input_is_not_an_error() -> None:
    payload = normalize_pi_payload(
        {"hook_event_name": "PostToolUse", "tool_name": "read", "tool_input": "nope"}
    )
    assert payload["tool_input"] == {}
    assert payload["tool_response"] == {"file": {"filePath": "", "content": ""}}


# ---------------------------------------------------------------------------
# Dispatch through the real entry point
# ---------------------------------------------------------------------------


def test_pi_session_start_emits_freshness_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repowise_repo(tmp_path)
    monkeypatch.setattr(
        "repowise.cli.commands.augment_cmd.session_start._git_head",
        lambda p: _INDEXED,
    )
    result = _invoke_augment(
        CliRunner(),
        {"hook_event_name": "SessionStart", "cwd": str(tmp_path), "session_id": "s1"},
        monkeypatch,
    )
    assert result.exit_code == 0
    assert "current" in _hook_output(result.output)["additionalContext"]


def test_pi_session_start_outside_a_repo_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke_augment(
        CliRunner(),
        {"hook_event_name": "SessionStart", "cwd": str(tmp_path), "session_id": "s1"},
        monkeypatch,
    )
    assert result.exit_code == 0
    assert result.output == ""


def test_pi_first_read_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repowise_repo(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = _invoke_augment(
        CliRunner(),
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "read",
            "tool_input": {"path": str(target)},
            "tool_response": {"content": [{"type": "text", "text": "x = 1\n"}]},
            "cwd": str(tmp_path),
            "session_id": "s1",
        },
        monkeypatch,
    )
    assert result.exit_code == 0
    # A first read of a small file earns no notice: silence is the
    # correct answer, and the run proves the Pi payload reached the
    # shared read handler without an exception.
    assert result.output == ""


def test_pi_bash_commit_warns_on_stale_wiki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": _INDEXED}), encoding="utf-8"
    )
    result = _invoke_augment(
        CliRunner(),
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "bash",
            "tool_input": {"command": "git commit -m 'add thing'"},
            "tool_response": {"content": [{"type": "text", "text": "[main abc] add thing"}]},
            "cwd": str(repo),
            "session_id": "s1",
        },
        monkeypatch,
    )
    assert result.exit_code == 0
    assert "repowise" in _hook_output(result.output)["additionalContext"]


def _indexed_repo(tmp_path: Path, files: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("CREATE TABLE repositories (id TEXT, local_path TEXT)")
    con.execute("INSERT INTO repositories VALUES ('r1', ?)", (str(repo),))
    con.execute(
        "CREATE TABLE graph_nodes (repository_id TEXT, node_id TEXT, node_type TEXT, "
        "pagerank REAL)"
    )
    for node_id in files:
        con.execute("INSERT INTO graph_nodes VALUES ('r1', ?, 'file', 0.5)", (node_id,))
        on_disk = repo / node_id
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text("x", encoding="utf-8")
    con.commit()
    con.close()
    return repo


def test_pi_failed_read_rescues_enoent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _indexed_repo(tmp_path, ("core/missing.py",))
    result = _invoke_augment(
        CliRunner(),
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "read",
            "tool_input": {"path": "src/missing.py"},
            "error": "Error: ENOENT: no such file or directory, open 'src/missing.py'",
            "cwd": str(repo),
            "session_id": "s1",
        },
        monkeypatch,
    )
    assert result.exit_code == 0
    context = _hook_output(result.output)["additionalContext"]
    assert "core/missing.py" in context
    assert "not in this tree" in context


def test_pi_failed_read_with_ambiguous_basename_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _indexed_repo(tmp_path, ("a/missing.py", "b/missing.py"))
    result = _invoke_augment(
        CliRunner(),
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "read",
            "tool_input": {"path": "src/missing.py"},
            "error": "Error: ENOENT: no such file or directory, open 'src/missing.py'",
            "cwd": str(repo),
            "session_id": "s1",
        },
        monkeypatch,
    )
    assert result.exit_code == 0
    assert result.output == ""


def test_pi_unknown_event_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke_augment(
        CliRunner(),
        {"hook_event_name": "TurnEnd", "cwd": str(tmp_path)},
        monkeypatch,
    )
    assert result.exit_code == 0
    assert result.output == ""
