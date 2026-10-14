"""git-ai v3 authorship notes on ``refs/notes/ai``: write ours, remove only ours.

Format: Git AI Standard v3.0.0 (``specs/git_ai_standard_v3.0.0.md`` in
github.com/git-ai-project/git-ai, last changed by commit ``e2411d98``,
2026-05-18). An attestation section (file path at column 0, two-space indented
``s_<14hex>::t_<14hex> <ranges>`` lines), a ``---`` line, then JSON metadata
with ``schema_version: "authorship/3.0.0"``, ``base_commit_sha``, ``prompts``
and ``sessions``.

What we put in a note is narrower than the format allows: ``agent_id.id`` is
the opaque session hash, never the agent's conversation id, and there is no
``human_author``, ``humans`` or ``custom_attributes`` - a note is readable by
everyone with repo access.

A commit that already has a note is never written: the note is someone else's
(git-ai, another machine) and the spec allows one per commit. Which notes are
ours is recorded by note blob id in the sidecar ledger, so purge removes our
notes, including the copies ``notes.rewriteRef`` carried across a rebase (same
blob), and never a foreign one.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from pathlib import Path

from .store import NOTES_LEDGER, append_jsonl, load_state, read_jsonl, save_state, sidecar_dir

NOTES_REF = "refs/notes/ai"
SCHEMA_VERSION = "authorship/3.0.0"
#: The fetch refspec a clone needs to receive teammates' notes.
FETCH_REFSPEC = f"+{NOTES_REF}:{NOTES_REF}"


def _git(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=None if stdin is not None else subprocess.DEVNULL,
        check=False,
    )


def session_key(tool: str, session: str) -> str:
    """``s_`` + SHA-256(``<tool>:<session>``)[:14], the spec's session id rule."""
    return "s_" + hashlib.sha256(f"{tool}:{session}".encode()).hexdigest()[:14]


def format_ranges(lines: list[int]) -> str:
    """Sorted 1-indexed lines as ``1-4,9,12-13``."""
    out: list[str] = []
    ordered = sorted(set(lines))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(out)


def _quote(path: str) -> str:
    return f'"{path}"' if any(ch in path for ch in " \t\n") else path


def render_note(commit: str, sessions: list[dict]) -> str:
    """One commit's note from binder sessions.

    Each session is ``{"tool", "session", "model", "files": {path: [line, ...]}}``
    with ``tool`` the git-ai tool name (``claude``, ``codex``, ``cursor``).
    """
    by_path: dict[str, list[str]] = {}
    meta: dict[str, dict] = {}
    for entry in sessions:
        key = session_key(entry["tool"], entry["session"])
        trace = f"t_{secrets.token_hex(7)}"
        meta[key] = {
            "agent_id": {
                "tool": entry["tool"],
                "id": entry["session"],
                "model": entry.get("model") or "unknown",
            }
        }
        for path, lines in entry["files"].items():
            by_path.setdefault(path, []).append(f"  {key}::{trace} {format_ranges(lines)}")
    attestation = []
    for path in sorted(by_path):
        attestation.append(_quote(path))
        attestation.extend(by_path[path])
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "base_commit_sha": commit,
        "prompts": {},
        "sessions": meta,
    }
    return "\n".join([*attestation, "---", json.dumps(metadata, indent=2, sort_keys=True)]) + "\n"


def _note_blob(repo: Path, commit: str) -> str | None:
    result = _git(repo, "notes", f"--ref={NOTES_REF}", "list", commit)
    blob = result.stdout.strip()
    return blob if result.returncode == 0 and blob else None


def write_note(repo: Path, commit: str, text: str) -> bool:
    """Attach *text* to *commit* unless it already has a note; True when written."""
    if _note_blob(repo, commit) is not None:
        return False
    if _git(repo, "notes", f"--ref={NOTES_REF}", "add", "-F", "-", commit, stdin=text).returncode:
        return False
    blob = _note_blob(repo, commit)
    if blob:
        append_jsonl(sidecar_dir(repo) / NOTES_LEDGER, [{"commit": commit, "blob": blob}])
    return blob is not None


def _config_values(repo: Path, key: str) -> list[str]:
    result = _git(repo, "config", "--get-all", key)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def ensure_rewrite_ref(repo: Path) -> bool:
    """Add ``notes.rewriteRef = refs/notes/ai`` to the repo config if missing.

    Git then copies the note when ``commit --amend`` or ``rebase`` (including
    a squash) rewrites the commit. True when this call added it; the state file
    remembers so purge can take it back out.
    """
    if NOTES_REF in _config_values(repo, "notes.rewriteRef"):
        return False
    if _git(repo, "config", "--add", "notes.rewriteRef", NOTES_REF).returncode:
        return False
    state = load_state(repo)
    state["rewrite_ref_added"] = True
    save_state(repo, state)
    return True


def _mentions_notes(refspec: str) -> bool:
    return NOTES_REF in refspec or "refs/notes/*" in refspec


def refspec_status(repo: Path, remote: str = "origin") -> dict[str, bool]:
    """Whether notes would reach teammates: remote, fetch, push and rewrite config."""
    has_remote = bool(_config_values(repo, f"remote.{remote}.url"))
    return {
        "remote": has_remote,
        "fetch": any(_mentions_notes(r) for r in _config_values(repo, f"remote.{remote}.fetch")),
        "push": any(_mentions_notes(r) for r in _config_values(repo, f"remote.{remote}.push")),
        "rewrite_ref": NOTES_REF in _config_values(repo, "notes.rewriteRef"),
    }


def purge_notes(repo: Path) -> int:
    """Remove every note whose blob Repowise wrote; returns how many went.

    Also takes out the ``notes.rewriteRef`` entry if we added it. Remote notes
    are never touched; the caller prints the push that would delete them.
    """
    ours = {row.get("blob") for row in read_jsonl(sidecar_dir(repo) / NOTES_LEDGER)}
    ours.discard(None)
    removed = 0
    if ours:
        listing = _git(repo, "notes", f"--ref={NOTES_REF}", "list")
        for line in listing.stdout.splitlines():
            parts = line.split()
            if (
                len(parts) == 2
                and parts[0] in ours
                and not _git(repo, "notes", f"--ref={NOTES_REF}", "remove", parts[1]).returncode
            ):
                removed += 1
    if load_state(repo).get("rewrite_ref_added"):
        _git(repo, "config", "--unset-all", "notes.rewriteRef", f"^{NOTES_REF}$")
    return removed
