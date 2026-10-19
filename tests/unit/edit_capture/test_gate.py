"""The capture consent gate: enabled x capture x agents x DO_NOT_TRACK, and the hook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd.edit_capture import capture_edit
from repowise.core.edit_capture.store import EVENTS, capture_level, sidecar_dir

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "attribution_capture"

_ON = "attribution:\n  capture: {level}\n  agents: [claude-code, codex]\n"


@pytest.mark.parametrize(
    ("repo_cfg", "user_cfg", "dnt", "agent", "expected"),
    [
        (_ON.format(level="local"), _ON.format(level="local"), False, "claude_code", "local"),
        (_ON.format(level="notes"), _ON.format(level="notes"), False, "codex", "notes"),
        # stricter wins
        (_ON.format(level="notes"), _ON.format(level="local"), False, "codex", "local"),
        # the user never opted in
        (_ON.format(level="local"), None, False, "claude_code", "off"),
        # the repo never opted in
        (None, _ON.format(level="local"), False, "claude_code", "off"),
        # agent not listed
        (_ON.format(level="local"), _ON.format(level="local"), False, "cursor", "off"),
        # DO_NOT_TRACK beats everything
        (_ON.format(level="notes"), _ON.format(level="notes"), True, "claude_code", "off"),
        # attribution switched off
        (
            "attribution:\n  enabled: false\n  capture: local\n  agents: [claude-code]\n",
            _ON.format(level="local"),
            False,
            "claude_code",
            "off",
        ),
    ],
)
def test_capture_level_matrix(
    tmp_path: Path,
    user_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo_cfg: str | None,
    user_cfg: str | None,
    dnt: bool,
    agent: str,
    expected: str,
) -> None:
    root = tmp_path / "r"
    (root / ".repowise").mkdir(parents=True)
    if repo_cfg is not None:
        (root / ".repowise" / "config.yaml").write_text(repo_cfg, encoding="utf-8")
    if user_cfg is not None:
        user_config.write_text(user_cfg, encoding="utf-8")
    if dnt:
        monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert capture_level(root, agent) == expected


def _payload_in(root: Path, fixture: str) -> dict:
    payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    payload["cwd"] = str(root)
    payload["tool_input"]["file_path"] = str(root / "src" / "billing.py")
    return payload


def test_hook_writes_nothing_when_capture_is_off(tmp_path: Path, user_config: Path) -> None:
    root = tmp_path / "r"
    (root / ".repowise").mkdir(parents=True)
    assert capture_edit("claude-code", _payload_in(root, "claude_code_edit.json")) == 0
    assert not sidecar_dir(root).exists()


def test_hook_records_hashes_only(tmp_path: Path, user_config: Path) -> None:
    root = (tmp_path / "r").resolve()
    (root / ".repowise").mkdir(parents=True)
    (root / ".repowise" / "config.yaml").write_text(_ON.format(level="local"), encoding="utf-8")
    user_config.write_text(_ON.format(level="local"), encoding="utf-8")
    payload = _payload_in(root, "claude_code_edit.json")
    assert capture_edit("claude-code", payload) == 1
    raw = (sidecar_dir(root) / EVENTS).read_text(encoding="utf-8")
    (event,) = [json.loads(line) for line in raw.splitlines()]
    assert event["agent"] == "claude_code" and event["path"] == "src/billing.py"
    assert len(event["hashes"]) == 2 and event["session"] and event["call"]
    # Never code, never the raw session id, never a prompt.
    assert "subtotal" not in raw and payload["session_id"] not in raw


def test_cursor_payload_never_stores_the_email(tmp_path: Path, user_config: Path) -> None:
    root = (tmp_path / "r").resolve()
    (root / ".repowise").mkdir(parents=True)
    cfg = "attribution:\n  capture: local\n  agents: [cursor]\n"
    (root / ".repowise" / "config.yaml").write_text(cfg, encoding="utf-8")
    user_config.write_text(cfg, encoding="utf-8")
    payload = json.loads((FIXTURES / "cursor_after_file_edit.json").read_text(encoding="utf-8"))
    payload["workspace_roots"] = [str(root)]
    payload["file_path"] = str(root / "src" / "billing.py")
    assert capture_edit("cursor", payload) == 1
    raw = (sidecar_dir(root) / EVENTS).read_text(encoding="utf-8")
    assert "dev@example.com" not in raw and '"agent":"cursor"' in raw


def test_augment_routes_edit_payloads_to_capture(
    tmp_path: Path, user_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from repowise.cli.commands.augment_cmd.command import _run_augment

    root = (tmp_path / "r").resolve()
    (root / ".repowise").mkdir(parents=True)
    cfg = "attribution:\n  capture: local\n  agents: [cursor, codex]\n"
    (root / ".repowise" / "config.yaml").write_text(cfg, encoding="utf-8")
    user_config.write_text(cfg, encoding="utf-8")

    cursor = json.loads((FIXTURES / "cursor_after_file_edit.json").read_text(encoding="utf-8"))
    cursor["workspace_roots"] = [str(root)]
    cursor["file_path"] = str(root / "src" / "billing.py")
    codex = json.loads((FIXTURES / "codex_apply_patch.json").read_text(encoding="utf-8"))
    codex["cwd"] = str(root)
    for client, payload in (("cursor", cursor), ("codex", codex)):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        _run_augment(client=client)
    events = [json.loads(x) for x in (sidecar_dir(root) / EVENTS).read_text().splitlines()]
    assert sorted(e["path"] for e in events) == ["src/billing.py", "src/billing.py", "src/report.py"]
    assert {e["agent"] for e in events} == {"cursor", "codex"}
