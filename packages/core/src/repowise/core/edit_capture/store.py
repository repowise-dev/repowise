"""The consent gate and the local sidecar edit events and bindings live in.

Everything sits under ``.repowise/attribution/`` (gitignored with the rest of
``.repowise/``):

* ``events.jsonl`` - pending edit events: agent, opaque session id, model,
  tool, repo-relative path, line hashes, timestamp. Never code, prompts or a
  person's identity.
* ``bindings.jsonl`` - commits the binder attributed, with line ranges.
* ``notes.jsonl`` - the git-ai notes Repowise wrote (commit and note blob), so
  purge can remove exactly those and nothing else.
* ``state.json`` - the binding cursor and whether we added ``notes.rewriteRef``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .edits import CapturedCall
from .hashing import line_hash, opaque_id

EVENTS = "events.jsonl"
BINDINGS = "bindings.jsonl"
NOTES_LEDGER = "notes.jsonl"
STATE = "state.json"

#: Pending events older than this are dropped at the next binding: an edit not
#: committed within two weeks is more likely reverted than waiting.
EVENT_TTL_SECONDS = 14 * 24 * 3600


def sidecar_dir(repo_root: Path | str) -> Path:
    return Path(repo_root) / ".repowise" / "attribution"


def capture_level(repo_root: Path | str, agent_slug: str) -> str:
    """``off``, ``local`` or ``notes`` for *agent_slug* in this repo.

    Ordered cheapest first, because every hooked edit asks: ``DO_NOT_TRACK``,
    then a substring test on the repo config (capture needs the repo to opt in,
    so a file that never says ``capture`` is off without a YAML parse), and
    only then the full stricter-wins resolution.
    """
    from repowise.core.platform.telemetry import _env_truthy

    if _env_truthy("DO_NOT_TRACK"):
        return "off"
    try:
        text = (Path(repo_root) / ".repowise" / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return "off"
    if "capture" not in text:
        return "off"
    from repowise.core.attribution import load_attribution_settings

    settings = load_attribution_settings(repo_root)
    if settings.capture == "off" or agent_slug.replace("_", "-") not in settings.agents:
        return "off"
    return settings.capture


def record_call(repo_root: Path, agent_slug: str, call: CapturedCall) -> int:
    """Append one event per edited file inside *repo_root*; returns how many."""
    from repowise.core.sessions.events import relative_files

    cwd = (call.cwd or str(repo_root)).replace("\\", "/").rstrip("/")
    lines: list[str] = []
    now = time.time()
    for edit in call.edits:
        path = edit.path.replace("\\", "/")
        if not (path.startswith("/") or (len(path) > 1 and path[1] == ":")):
            path = f"{cwd}/{path}"
        rel = relative_files([path], repo_root)
        if not rel:
            continue
        event = {
            "v": 1,
            "ts": now,
            "agent": agent_slug,
            "session": opaque_id(call.session_id) if call.session_id else "",
            "model": call.model,
            "tool": call.tool,
            "call": opaque_id(call.call_id) if call.call_id else "",
            "path": rel[0],
            "hashes": [line_hash(line) for line in edit.added],
        }
        lines.append(json.dumps(event, separators=(",", ":")) + "\n")
    if not lines:
        return 0
    append_lines(sidecar_dir(repo_root) / EVENTS, lines)
    return len(lines)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every object line of *path*; a torn or foreign line is skipped."""
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                with contextlib.suppress(ValueError):
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
    except OSError:
        return []
    return rows


def append_lines(path: Path, lines: list[str]) -> None:
    """Append *lines* in one ``write`` on an ``O_APPEND`` fd.

    A buffered text write splits past 8 KB, so two hooks finishing together
    could interleave inside a line; one syscall keeps each batch whole.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, "".join(lines).encode("utf-8"))
    finally:
        os.close(fd)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        append_lines(path, [json.dumps(row, separators=(",", ":")) + "\n" for row in rows])


def load_state(repo_root: Path | str) -> dict[str, Any]:
    try:
        data = json.loads((sidecar_dir(repo_root) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(repo_root: Path | str, state: dict[str, Any]) -> None:
    directory = sidecar_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"{STATE}.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, directory / STATE)


def bound_commits(repo_root: Path | str) -> dict[str, str]:
    """``commit sha -> agent slug`` for every commit the binder attributed."""
    out: dict[str, str] = {}
    for row in read_jsonl(sidecar_dir(repo_root) / BINDINGS):
        sha, agent = row.get("commit"), row.get("agent")
        if isinstance(sha, str) and isinstance(agent, str) and agent:
            out[sha] = agent
    return out


def summary(repo_root: Path | str) -> dict[str, int]:
    """Counts for ``repowise attribution status``."""
    directory = sidecar_dir(repo_root)
    return {
        "pending_events": len(read_jsonl(directory / EVENTS)),
        "bound_commits": len(bound_commits(repo_root)),
        "notes_written": len(read_jsonl(directory / NOTES_LEDGER)),
    }


def delete_sidecar(repo_root: Path | str) -> bool:
    """Remove the whole sidecar; True when there was one."""
    directory = sidecar_dir(repo_root)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True
