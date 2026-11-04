"""Transcript backfill: one read loop and extractor, worktree path mapping, precision rules.

Transcripts are synthetic, shapes are real: Claude Code lines as the 2.1.x
writer records them (main and subagent transcripts), Codex rollouts as 0.154
records a code-mode ``exec`` cell (``tests/fixtures/attribution_capture``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from repowise.core.edit_capture import backfill as bf
from repowise.core.edit_capture.binder import bind_pending
from repowise.core.edit_capture.edits import CAPTURE_ADAPTERS, CapturedCall, FileEdit, codex_edits
from repowise.core.edit_capture.store import (
    EVENTS,
    load_state,
    read_jsonl,
    record_call,
    sidecar_dir,
)
from repowise.core.sessions.adapters import get_adapter
from repowise.core.sessions.events import ToolResult, ToolUse

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "attribution_capture"
DAY = 86400

LEDGER = [
    "def settle(ledger):",
    "    balance = sum(entry.amount for entry in ledger)",
    "    fees = compute_fees(balance, ledger.currency)",
    '    note = "settled \u2713"',
    "    audit_settlement(ledger, balance, fees)",
    "    return balance - fees",
]


def _body(name: str, n: int = bf.MIN_RUN_BACKFILL) -> list[str]:
    """*n* distinct substantive lines."""
    return [f"{name}_{i} = compute_{name}_value({i}, scale)" for i in range(n)]


REPORT = _body("report")
TAXES = _body("taxes")
OLD, ERR, SUB, LATER = _body("old"), _body("err"), _body("sub"), _body("later")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class ClaudeTranscript:
    """Writes Claude Code JSONL: an assistant ``tool_use`` line, then its result line."""

    def __init__(self, path: Path, cwd: str, *, subagent: bool = False) -> None:
        self.path, self.cwd, self.subagent = path, cwd, subagent
        self.lines: list[dict] = []
        self.n = 0

    def call(self, name: str, tool_input: dict, ts: float, *, result=None, error: bool = False, text: str = "ok") -> None:
        self.n += 1
        use_id = f"toolu_{self.n:04d}"
        base = {"sessionId": "sess-1", "cwd": self.cwd, "isSidechain": self.subagent}
        self.lines.append(
            base
            | {
                "type": "assistant",
                "timestamp": _iso(ts),
                "message": {
                    "id": f"msg_{self.n}",
                    "model": "claude-opus-5",
                    "content": [{"type": "tool_use", "id": use_id, "name": name, "input": tool_input}],
                },
            }
        )
        entry = base | {
            "type": "user",
            "timestamp": _iso(ts + 1),
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": use_id, "content": text, "is_error": error}
                ]
            },
        }
        if result is not None and not self.subagent:
            entry["toolUseResult"] = result
        self.lines.append(entry)

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(x) + "\n" for x in self.lines), encoding="utf-8")


def _codex_rollout(root: Path, repo_root: Path, ts: float) -> None:
    text = (FIXTURES / "codex_exec_cell_rollout.jsonl").read_text(encoding="utf-8")
    text = text.replace("REPO_ROOT", str(repo_root).replace("\\", "\\\\"))
    lines = []
    for i, line in enumerate(text.splitlines()):
        row = json.loads(line)
        row["timestamp"] = _iso(ts + i)
        lines.append(json.dumps(row, separators=(",", ":")))
    path = root / "2026" / "10" / "01" / "rollout-test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the shared extractor ------------------------------------------------------


def test_codex_exec_cell_patch_is_decoded_from_its_js_literal() -> None:
    lines = (FIXTURES / "codex_exec_cell_rollout.jsonl").read_text(encoding="utf-8").splitlines()
    adapter = get_adapter("codex")
    use_event, result_event = adapter.normalize(lines[2]), adapter.normalize(lines[3])
    (use,) = use_event.tool_uses
    assert use.name == "edit_file"
    (edit,) = adapter.file_edits(use, result_event.tool_results[0])
    assert edit.path == "src\\ledger.py" and edit.added == LEDGER


@pytest.mark.parametrize(
    "cell",
    [
        "const p = '*** Begin Patch\\n*** Add File: a.py\\n+it\\'s = \"x\"\\n*** End Patch';\nawait tools.apply_patch(p);",
        "const p = `*** Begin Patch\n*** Add File: a.py\n+it's = \"x\"\n*** End Patch`;\nawait tools.apply_patch(p);",
        'text(await tools.apply_patch("*** Begin Patch\\n*** Add File: a.py\\n+it\\x27s = \\"x\\"\\n*** End Patch"));',
    ],
    ids=["single-quoted", "template", "inline-hex-escape"],
)
def test_every_js_literal_form_decodes_the_same(cell: str) -> None:
    (edit,) = codex_edits("edit_file", {"command": cell}, None)
    assert (edit.path, edit.added) == ("a.py", ["it's = \"x\""])


def test_a_direct_patch_that_mentions_the_cell_api_is_still_a_direct_patch() -> None:
    patch = (
        "*** Begin Patch\n*** Add File: docs/codex.md\n"
        "+Code-mode cells call tools.apply_patch(patch) with a JavaScript literal.\n"
        "*** End Patch"
    )
    (edit,) = codex_edits("apply_patch", {"command": patch}, "Exit code: 0\nSuccess.")
    assert edit.path == "docs/codex.md" and len(edit.added) == 1


def test_only_the_literal_apply_patch_receives_is_read() -> None:
    cell = (
        'const draft = "*** Begin Patch\\n*** Add File: draft.py\\n+draft = 1\\n*** End Patch";\n'
        'let patch = "*** Begin Patch\\n*** Add File: old.py\\n+old = 1\\n*** End Patch";\n'
        'patch = "*** Begin Patch\\n*** Add File: real.py\\n+real = 1\\n*** End Patch";\n'
        "text(await tools.apply_patch(patch));\n"
    )
    assert [e.path for e in codex_edits("edit_file", {"command": cell}, None)] == ["real.py"]


@pytest.mark.parametrize("escape", ["\\x4g", "\\x+4", "\\u00 1", "\\u{+41}", "\\u{}"])
def test_a_malformed_hex_escape_leaves_the_literal_unread(escape: str) -> None:
    cell = f'await tools.apply_patch("*** Begin Patch\\n*** Add File: a.py\\n+v = {escape}\\n*** End Patch");'
    assert codex_edits("edit_file", {"command": cell}, None) == []


def test_an_interpolated_template_is_not_read() -> None:
    cell = "const p = `*** Begin Patch\n*** Add File: ${name}\n+x = 1\n*** End Patch`;\nawait tools.apply_patch(p);"
    assert codex_edits("edit_file", {"command": cell}, None) == []


@pytest.mark.parametrize(
    "response",
    [
        [{"type": "input_text", "text": "Script failed\nWall time 0.3 seconds\nOutput:\n Script error:"}],
        [{"type": "input_text", "text": "Script running with cell ID 4\nWall time 31.0 seconds\nOutput:\n"}],
        [{"type": "input_text", "text": "Script completed\nOutput:\napply_patch verification failed: x"}],
        "apply_patch verification failed: Failed to find expected lines in a.py",
        "Exit code: 1\nWall time: 0.1 seconds",
    ],
    ids=["script-failed", "still-running", "caught-in-cell", "direct-verification", "exit-code"],
)
def test_a_failed_or_unfinished_patch_wrote_nothing(response) -> None:
    patch = "*** Begin Patch\n*** Add File: a.py\n+value = compute_value()\n*** End Patch"
    assert codex_edits("apply_patch", {"command": patch}, response) == []
    assert codex_edits("apply_patch", {"command": patch}, "Exit code: 0\nSuccess.")


def test_the_live_codex_hook_reads_an_exec_cell_too() -> None:
    cell = json.loads((FIXTURES / "codex_exec_cell_rollout.jsonl").read_text().splitlines()[2])
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "s",
        "cwd": "/work/demo",
        "tool_name": "exec",
        "tool_input": {"command": cell["payload"]["input"]},
        "tool_response": [{"type": "input_text", "text": "Script completed\nOutput:\n {}"}],
        "tool_use_id": "call_ok",
        "model": "gpt-5.5-codex",
    }
    call = CAPTURE_ADAPTERS["codex"](payload)
    assert call is not None and call.edits[0].added == LEDGER


def test_a_claude_error_result_wrote_nothing() -> None:
    adapter = get_adapter("claude_code")
    use = ToolUse(id="t", name="Edit", input={"file_path": "a.py", "old_string": "x", "new_string": "y = compute()"})
    failed = ToolResult(tool_use_id="t", is_error=True, payload="Error: File has not been read yet.")
    assert adapter.file_edits(use, failed) == []


@pytest.mark.parametrize(
    ("text", "recovered"),
    [
        ("File created successfully at: C:\\work\\a.py", True),
        ("The file C:\\work\\a.py has been updated successfully.", False),
    ],
)
def test_a_subagent_write_counts_only_as_a_create(text: str, recovered: bool) -> None:
    """Subagent transcripts carry no toolUseResult; the result text says create or update."""
    adapter = get_adapter("claude_code")
    use = ToolUse(id="t", name="Write", input={"file_path": "C:\\work\\a.py", "content": "\n".join(TAXES)})
    edits = adapter.file_edits(use, ToolResult(tool_use_id="t", content=text))
    assert bool(edits) is recovered


# --- path mapping ----------------------------------------------------------------


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, str]:
    main = tmp_path / "repo"
    live = tmp_path / "wt" / "live"
    other = tmp_path / "other"
    for d in (main / "backend" / "app", live / "src", other / "src"):
        d.mkdir(parents=True)
    (main / "backend" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    posix = {k: str(v).replace("\\", "/") for k, v in {"main": main, "live": live, "other": other}.items()}
    posix["removed"] = str(tmp_path / "wt" / "gone").replace("\\", "/")
    return posix


def _map(layout: dict[str, str]) -> bf.PathMap:
    known = {"src/ledger.py", "README.md", "app/main.py", "src/index.ts", "packages/app/index.ts"}
    return bf.PathMap(layout["main"], [layout["live"]], known)


def test_paths_in_the_checkout_and_a_live_worktree_map(layout) -> None:
    m = _map(layout)
    assert m(f"{layout['main']}/src/ledger.py", "") == "src/ledger.py"
    assert m(f"{layout['live']}/README.md", "") == "README.md"
    # Windows separators and a path relative to the call's cwd.
    assert m(layout["live"].replace("/", "\\") + "\\src\\ledger.py", "") == "src/ledger.py"
    assert m("src\\ledger.py", layout["live"]) == "src/ledger.py"
    assert m("../src/ledger.py", f"{layout['live']}/docs") == "src/ledger.py"


def test_a_removed_worktree_maps_only_with_a_two_segment_path(layout) -> None:
    m = _map(layout)
    assert m(f"{layout['removed']}/src/ledger.py", "") == "src/ledger.py"
    assert m(f"{layout['removed']}/README.md", "") is None


def test_a_vanished_foreign_project_needs_a_longer_suffix(layout, tmp_path: Path) -> None:
    """A deleted directory that is not beside a worktree may have been any project."""
    m = _map(layout)
    gone = str(tmp_path / "deleted-app").replace("\\", "/")
    assert m(f"{gone}/src/index.ts", "") is None
    assert m(f"{gone}/packages/app/index.ts", "") == "packages/app/index.ts"
    # Inside a worktrees/ directory it is taken for one of ours.
    old = str(tmp_path / "x" / "worktrees" / "old").replace("\\", "/")
    assert m(f"{old}/src/index.ts", "") == "src/index.ts"


def test_another_repo_and_a_sibling_repo_never_map(layout) -> None:
    m = _map(layout)
    assert m(f"{layout['other']}/src/ledger.py", "") is None
    # backend/ is its own repo inside the checkout: backend/app/main.py is not ours.
    assert m(f"{layout['main']}/backend/app/main.py", "") is None
    assert m("src/ledger.py", "") is None  # relative with no cwd


def test_a_file_no_commit_touched_yet_maps_only_inside_our_own_tree(layout) -> None:
    m = _map(layout)
    assert m(f"{layout['live']}/src/new.py", "") == "src/new.py"
    assert m(f"{layout['main']}/backend/app/new.py", "") is None  # nested checkout
    assert m(f"{layout['removed']}/src/new.py", "") is None
    assert m(f"{layout['other']}/src/new.py", "") is None


# --- end to end ------------------------------------------------------------------


def _opt_in(repo, user_config: Path, agents: str = "[claude-code, codex]") -> None:
    cfg = f"attribution:\n  capture: local\n  agents: {agents}\n"
    repo.configure(cfg)
    user_config.write_text(cfg, encoding="utf-8")


def test_consent_needs_capture_and_the_agent_in_both_configs(repo, user_config, monkeypatch) -> None:
    assert bf.consent(repo.root, ["claude-code"])[0] == []
    _opt_in(repo, user_config, "[codex]")
    allowed, refused = bf.consent(repo.root, ["claude-code", "codex", "cursor"])
    assert allowed == ["codex"]
    assert "attribution.agents" in refused["claude-code"] and "transcript" in refused["cursor"]
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert bf.consent(repo.root, ["codex"])[0] == []


@pytest.fixture
def scene(repo, user_config, tmp_path: Path):
    """A repo with a live worktree, Claude and Codex transcripts, and one commit."""
    _opt_in(repo, user_config)
    wt = (tmp_path / "wt-feature").resolve()
    repo.git("worktree", "add", "-q", "-b", "feature", str(wt))
    now = time.time()
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"

    main = ClaudeTranscript(claude_root / "C--repo" / "s1.jsonl", str(wt))
    main.call("Write", {"file_path": str(wt / "src" / "taxes.py"), "content": "\n".join(TAXES) + "\n"},
              now - 3 * 3600, result={"type": "create", "filePath": "x", "content": "", "structuredPatch": []})
    # MultiEdit with CRLF replacements (a Windows agent): hashes like the LF commit.
    main.call("MultiEdit", {"file_path": str(wt / "src" / "report.py"),
                            "edits": [{"old_string": "", "new_string": "\r\n".join(REPORT[:2])},
                                      {"old_string": "pass", "new_string": "\r\n".join(REPORT[2:])}]},
              now - 2 * 3600, result={"filePath": "x"})
    # Made 20 days before the commit: outside the window.
    main.call("Edit", {"file_path": str(wt / "src" / "old.py"), "old_string": "a", "new_string": "\n".join(OLD)},
              now - 20 * DAY, result={"filePath": "x"})
    # A failed edit wrote nothing.
    main.call("Edit", {"file_path": str(wt / "src" / "err.py"), "old_string": "a", "new_string": "\n".join(ERR)},
              now - 3600, result="Error: File has not been read yet.", error=True)
    main.write()
    # A subagent transcript: no toolUseResult, so a Write is a create only by its result text.
    sub = ClaudeTranscript(claude_root / "C--repo" / "s1" / "subagents" / "agent-1.jsonl", str(wt), subagent=True)
    sub.call("Write", {"file_path": str(wt / "src" / "sub.py"), "content": "\n".join(SUB)},
             now - 3600, text=f"File created successfully at: {wt / 'src' / 'sub.py'}")
    sub.write()
    _codex_rollout(codex_root, wt, now - 3600)

    for rel, body in {
        "src/taxes.py": TAXES, "src/report.py": REPORT, "src/ledger.py": LEDGER,
        "src/old.py": OLD, "src/err.py": ERR, "src/sub.py": SUB,
    }.items():
        repo.write(rel, "\n".join(body) + "\n")
    sha = repo.commit("work")
    roots = {"claude-code": claude_root, "codex": codex_root}
    return repo, sha, roots


def test_backfill_binds_edits_from_both_agents_and_skips_what_did_not_land(scene) -> None:
    repo, sha, roots = scene
    result = bf.backfill(repo.root, ["claude-code", "codex"], capture="local", projects_roots=roots)
    assert result.transcripts == {"claude-code": 2, "codex": 1}
    (binding,) = result.bindings
    assert binding["commit"] == sha
    files = {p for s in binding["sessions"] for p in s["files"]}
    # Write create, MultiEdit, subagent Write create, Codex exec-cell patch.
    assert files == {"src/taxes.py", "src/report.py", "src/sub.py", "src/ledger.py"}
    by_agent = {s["agent"] for s in binding["sessions"]}
    assert by_agent == {"claude_code", "codex"}
    assert any(s["model"] == "gpt-5.5-codex" for s in binding["sessions"] if s["agent"] == "codex")
    assert result.covered_before == 0 and result.covered_after == 1


def test_backfill_records_its_run_and_reads_only_new_lines_next_time(scene) -> None:
    repo, sha, roots = scene
    bf.backfill(repo.root, ["claude-code", "codex"], capture="local", projects_roots=roots)
    state = load_state(repo.root)
    assert state["backfill"]["bound"] == 1 and state["cursor"] == sha
    cursors = json.loads((sidecar_dir(repo.root) / bf.CURSORS).read_text(encoding="utf-8"))
    assert all(c["offset"] == Path(f).stat().st_size for f, c in cursors.items())
    again = bf.backfill(repo.root, ["claude-code", "codex"], capture="local", projects_roots=roots)
    assert again.edits == 0 and again.bindings == []
    # One line appended: the next run reads that edit and nothing before it.
    t = ClaudeTranscript(roots["claude-code"] / "C--repo" / "s1.jsonl", str(repo.root))
    t.n = 100
    t.call("Write", {"file_path": str(repo.root / "src" / "next.py"), "content": "\n".join(LATER)},
           time.time() - 60, result={"type": "create"})
    with open(t.path, "a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(x) + "\n" for x in t.lines))
    third = bf.backfill(repo.root, ["claude-code"], capture="local", projects_roots=roots)
    assert third.edits == 1


def test_backfill_leaves_the_live_cursor_for_waiting_hook_events(scene) -> None:
    repo, _, roots = scene
    live = _body("live", 3)
    call = CapturedCall(tool="Edit", edits=[FileEdit("src/live.py", live)], session_id="h", cwd=str(repo.root))
    assert record_call(repo.root, "claude_code", call) == 1
    repo.write("src/live.py", "\n".join(live) + "\n")
    live_sha = repo.commit("live work")
    bf.backfill(repo.root, ["claude-code", "codex"], capture="local", projects_roots=roots)
    assert "cursor" not in load_state(repo.root)
    assert [b["commit"] for b in bind_pending(repo.root, capture="local")] == [live_sha]


def test_edits_outside_any_path_of_the_repo_are_dropped_as_read(layout, tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    t = ClaudeTranscript(path, layout["other"])
    for name in ("ours", "theirs"):
        rel = "src/ledger.py" if name == "ours" else "lib/elsewhere.py"
        t.call("Write", {"file_path": f"{layout['main']}/{rel}" if name == "ours" else f"{layout['other']}/{rel}",
                         "content": "\n".join(_body(name))}, time.time(), result={"type": "create"})
    t.write()
    adapter = get_adapter("claude_code")
    recorder = bf.TranscriptEditRecorder(keep=_map(layout))
    for _ in recorder.observe(adapter, "claude_code", adapter.iter_events(path)):
        pass
    assert recorder.edits == 2 and [r["path"] for r, _ in recorder._rows] == [f"{layout['main']}/src/ledger.py"]


def test_unbound_recent_edits_wait_for_the_next_commit(scene, tmp_path: Path) -> None:
    repo, _, roots = scene
    t = ClaudeTranscript(roots["claude-code"] / "C--repo" / "s2.jsonl", str(repo.root))
    t.call("Write", {"file_path": str(repo.root / "src" / "later.py"),
                     "content": "\n".join(LATER)},
           time.time() - 60, result={"type": "create"})
    t.write()
    result = bf.backfill(repo.root, ["claude-code"], capture="local", projects_roots=roots)
    pending = read_jsonl(sidecar_dir(repo.root) / EVENTS)
    assert result.handed_over == len(pending) and {r["path"] for r in pending} >= {"src/later.py"}
    assert all(set(r) >= {"hashes", "agent", "ts", "path"} and "content" not in r for r in pending)


def test_backfill_holds_a_run_below_the_minimum(repo, user_config, tmp_path: Path) -> None:
    _opt_in(repo, user_config)
    short = _body("short", bf.MIN_RUN_BACKFILL - 1)
    t = ClaudeTranscript(tmp_path / "claude" / "p" / "s.jsonl", str(repo.root))
    t.call("Write", {"file_path": str(repo.root / "a.py"), "content": "\n".join(short)}, time.time() - 600,
           result={"type": "create"})
    t.write()
    repo.write("a.py", "\n".join(short) + "\n")
    repo.commit("short run")
    result = bf.backfill(repo.root, ["claude-code"], capture="local", projects_roots={"claude-code": tmp_path / "claude"})
    assert result.bindings == []
