"""git-ai v3 notes: format, rewriteRef, never overwrite, purge only ours.

The format is checked against the spec's own example
(``tests/fixtures/attribution_capture/git_ai_v3_spec_example.txt``, Git AI
Standard v3.0.0 section 1.2.5, git-ai-project/git-ai commit ``e2411d98``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repowise.core.edit_capture.binder import bind_pending
from repowise.core.edit_capture.edits import CapturedCall, FileEdit
from repowise.core.edit_capture.notes import (
    NOTES_REF,
    format_ranges,
    purge_notes,
    refspec_status,
    render_note,
    session_key,
)
from repowise.core.edit_capture.store import record_call
from repowise.core.ingestion.git_indexer.agent_provenance import parse_git_ai_note

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "attribution_capture"
BODY = [
    "def settle(ledger):",
    "    balance = sum(entry.amount for entry in ledger)",
    "    fees = compute_fees(balance, ledger.currency)",
    "    return balance - fees",
]
_KEY_LINE = re.compile(r"^  s_[0-9a-f]{14}::t_[0-9a-f]{14} [0-9,-]+$")


def _shape(note: str) -> tuple[list[str], dict]:
    attestation, divider, metadata = note.partition("\n---\n")
    assert divider
    return attestation.splitlines(), json.loads(metadata)


def test_rendered_note_has_the_spec_shape() -> None:
    spec_lines, spec_meta = _shape(
        (FIXTURES / "git_ai_v3_spec_example.txt").read_text(encoding="utf-8")
    )
    note = render_note(
        "a" * 40,
        [{"tool": "claude", "session": "0123456789abcdef", "model": None, "files": {"src/my file.py": [1, 2, 3, 7]}}],
    )
    lines, meta = _shape(note)
    assert lines[0] == '"src/my file.py"'
    assert _KEY_LINE.match(lines[1]) and lines[1].endswith(" 1-3,7")
    assert meta["schema_version"] == spec_meta["schema_version"] == "authorship/3.0.0"
    assert {"base_commit_sha", "prompts"} <= set(spec_meta) & set(meta)
    (session,) = meta["sessions"].values()
    assert set(session["agent_id"]) == set(next(iter(spec_meta["sessions"].values()))["agent_id"])
    # Narrower than the spec allows: no person identity, no custom attributes.
    assert "human_author" not in session and "humans" not in meta
    assert all(_KEY_LINE.match(line) for line in spec_lines if line.startswith("  s_"))
    assert parse_git_ai_note(note).agent == "claude"
    # render_note writes "unknown" for a missing model; that is not a model.
    assert parse_git_ai_note(note).model is None


def test_note_model_is_the_agents_most_common() -> None:
    def session(sid: str, model: str | None) -> dict:
        return {"tool": "codex", "session": sid, "model": model, "files": {"a.py": [1]}}

    note = render_note("a" * 40, [session("s1", "gpt-5.5-codex"), session("s2", "gpt-5.5-codex")])
    assert parse_git_ai_note(note).model == "gpt-5.5-codex"
    tied = render_note("a" * 40, [session("s1", "gpt-5.5-codex"), session("s2", "o4")])
    assert parse_git_ai_note(tied).model is None


def test_session_key_and_ranges_follow_the_spec() -> None:
    assert re.fullmatch(r"s_[0-9a-f]{14}", session_key("claude", "abc"))
    assert format_ranges([5, 1, 2, 3, 9, 10]) == "1-3,5,9-10"


def _capture(repo, path: str, lines: list[str]) -> None:
    call = CapturedCall(tool="Edit", edits=[FileEdit(path, lines)], session_id="sess", cwd=str(repo.root))
    record_call(repo.root, "claude_code", call)


def test_notes_level_writes_a_note_and_sets_rewrite_ref(repo) -> None:
    _capture(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    sha = repo.commit("agent")
    assert len(bind_pending(repo.root, capture="notes")) == 1
    note = repo.git("notes", f"--ref={NOTES_REF}", "show", sha)
    assert note.startswith("src/a.py\n  s_") and " 1-4\n" in note
    assert parse_git_ai_note(note).agent == "claude"
    assert NOTES_REF in repo.git("config", "--get-all", "notes.rewriteRef")
    assert refspec_status(repo.root)["rewrite_ref"]

    # The note follows an amend because of notes.rewriteRef.
    repo.git("commit", "--amend", "-q", "-m", "agent, reworded")
    amended = repo.git("rev-parse", "HEAD").strip()
    assert amended != sha
    assert "src/a.py" in repo.git("notes", f"--ref={NOTES_REF}", "show", amended)


def test_local_level_writes_no_note(repo) -> None:
    _capture(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("agent")
    assert len(bind_pending(repo.root, capture="local")) == 1
    assert repo.git("notes", f"--ref={NOTES_REF}", "list") == ""


def test_a_foreign_note_is_never_overwritten_or_purged(repo) -> None:
    _capture(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    sha = repo.commit("agent")
    foreign = 'src/a.py\n  h_0123456789abcd 1\n---\n{"schema_version": "authorship/3.0.0", "base_commit_sha": "x", "prompts": {}}'
    repo.git("notes", f"--ref={NOTES_REF}", "add", "-m", foreign, sha)
    assert len(bind_pending(repo.root, capture="notes")) == 1
    assert "h_0123456789abcd" in repo.git("notes", f"--ref={NOTES_REF}", "show", sha)
    assert purge_notes(repo.root) == 0
    assert "h_0123456789abcd" in repo.git("notes", f"--ref={NOTES_REF}", "show", sha)


def test_purge_removes_only_our_notes_and_our_rewrite_ref(repo) -> None:
    repo.write("other.py", "print('x')\n")
    other = repo.commit("other")
    repo.git("notes", f"--ref={NOTES_REF}", "add", "-m", "foreign\n---\n{}", other)
    _capture(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    ours = repo.commit("agent")
    bind_pending(repo.root, capture="notes")
    assert purge_notes(repo.root) == 1
    listing = repo.git("notes", f"--ref={NOTES_REF}", "list")
    assert other in listing and ours not in listing
    assert not refspec_status(repo.root)["rewrite_ref"]


def test_refspec_status_reads_fetch_and_push(repo) -> None:
    assert refspec_status(repo.root) == {"remote": False, "fetch": False, "push": False, "rewrite_ref": False}
    repo.git("remote", "add", "origin", "https://example.invalid/demo.git")
    repo.git("config", "--add", "remote.origin.fetch", f"+{NOTES_REF}:{NOTES_REF}")
    status = refspec_status(repo.root)
    assert status["remote"] and status["fetch"] and not status["push"]
