"""The hooks' side of files not opened: credit a replaced Read, note a later one.

A collapsed re-read credits its file once for the session; a later Read of a
credited path is noted, after the response, so the credit lapses. A repowise
MCP answer marks the files it served, so a Read of one is noted too. None of
this ever changes what the hook returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import _handle_post_tool_use, read_state
from repowise.cli.commands.augment_cmd.read_state import _load_session_state
from repowise.cli.commands.augment_cmd.served_reads import _handle_mcp_read_post
from repowise.core.savings.service import load_report

SESSION = "sess-files"
_BODY = "\n".join(f"line {i} carrying enough text to be worth not repeating" for i in range(120))


def _make_repo(path: Path) -> Path:
    (path / ".repowise").mkdir(parents=True)
    (path / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_reread: true\n", encoding="utf-8"
    )
    (path / "a.py").write_text(_BODY, encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("REPOWISE_HOOK_UPDATED_OUTPUT", "1")
    return _make_repo(tmp_path)


def _read(repo: Path, body: str = _BODY):
    result = _handle_post_tool_use(
        "Read",
        {"file_path": str(repo / "a.py")},
        {
            "type": "text",
            "file": {
                "filePath": str(repo / "a.py"),
                "content": body,
                "numLines": body.count("\n") + 1,
                "startLine": 1,
                "totalLines": body.count("\n") + 1,
            },
        },
        str(repo),
        session_id=SESSION,
    )
    if result.on_emitted is not None:
        result.on_emitted()
    return result


def _files(repo: Path) -> tuple[int, int]:
    report = load_report(repo)
    assert report is not None
    return report.files_not_opened, report.tool_calls_answered


def test_a_collapsed_reread_credits_its_file_and_a_later_read_cancels_it(repo: Path) -> None:
    _read(repo)
    collapsed = _read(repo)
    assert collapsed.replacement is not None
    assert _files(repo) == (1, 1)
    assert "a.py" in _load_session_state(repo, SESSION)["credited"]

    # The file changes, so the next Read is served whole: the agent opened it.
    changed = _BODY + "\nnew line"
    (repo / "a.py").write_text(changed, encoding="utf-8")
    served = _read(repo, changed)
    assert served.replacement is None
    assert _files(repo) == (0, 1)


def test_an_uncredited_read_writes_nothing(repo: Path) -> None:
    result = _read(repo)
    assert result.replacement is None
    # No ledger was ever needed for a plain first Read.
    assert load_report(repo) is None or _files(repo) == (0, 0)


def _changed_read(repo: Path):
    _read(repo)
    _read(repo)
    changed = _BODY + "\nnew line"
    (repo / "a.py").write_text(changed, encoding="utf-8")
    return _read(repo, changed)


def test_a_failing_read_note_never_changes_the_hook_result(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _changed_read(_make_repo(tmp_path / "control"))

    def boom(*_args, **_kwargs):
        raise RuntimeError("forced")

    from repowise.cli.commands.augment_cmd import _shared

    monkeypatch.setattr(read_state, "note_credited", boom)
    monkeypatch.setattr(_shared, "record_file_read", boom)
    monkeypatch.setattr(read_state, "_then", boom)
    forced = _changed_read(repo)
    assert (forced.replacement, forced.context) == (control.replacement, control.context)


def test_an_mcp_answer_marks_the_files_it_served(repo: Path) -> None:
    output = json.dumps(
        {
            "targets": {
                "a.py": {"type": "file", "docs": {}},
                "pkg": {"type": "module"},
                "b.py::f": {"type": "symbol", "skeleton": {"of_file": "b.py"}},
            },
            "citations": ["c.py"],
        }
    )
    _handle_mcp_read_post([{"type": "text", "text": output}], str(repo), SESSION)
    assert _load_session_state(repo, SESSION)["credited"] == ["a.py", "b.py", "c.py"]


def test_a_broken_mcp_payload_marks_nothing_and_never_raises(repo: Path) -> None:
    _handle_mcp_read_post({"targets": ["not", "a", "dict"], "citations": 7}, str(repo), SESSION)
    assert _load_session_state(repo, SESSION)["credited"] == []
