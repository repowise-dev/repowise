"""Leaf helpers shared across the augment submodules.

These import nothing from sibling submodules so the package import graph
stays acyclic: ``search``/``read_state``/``bash_staleness``/``codex`` all
depend on ``_shared``, never the other way around.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple


class HookResult(NamedTuple):
    """What one PostToolUse handler wants the hook to do.

    ``context`` is appended to what the agent sees (``additionalContext``);
    ``replacement`` *replaces* the tool result outright
    (``updatedToolOutput``). Most handlers only ever set ``context``, so
    :func:`as_result` lifts their bare ``str | None`` into this shape and the
    two-field response is assembled in exactly one place.

    ``on_emitted`` is bookkeeping the handler wants done *after* the response
    reaches the agent — savings accounting, counters. Anything here is off the
    critical path by construction, which is the only way to keep it honest:
    a docstring promising "this runs after the response" is not enforcement,
    and the first version of this got it wrong.
    """

    context: str | None = None
    replacement: str | None = None
    on_emitted: Callable[[], None] | None = None

    def __bool__(self) -> bool:
        return bool(self.context or self.replacement)


def as_result(value: HookResult | str | None) -> HookResult:
    """Normalize a handler return into a :class:`HookResult`."""
    if isinstance(value, HookResult):
        return value
    return HookResult(context=value or None)

#: Wall clock at the first moment repowise code runs in this hook process.
#: Every ledger row carries the elapsed time to its own write, which is the
#: part of hook latency repowise controls. It is a *lower bound* on what the
#: user waits: the interpreter start and the click import happen before this
#: module loads, and the response is written after. Claude Code records the
#: true end-to-end figure as ``attachment.durationMs``, which the transcript
#: pass in :mod:`repowise.core.sessions.efficacy` writes over the top.
_T0 = time.perf_counter()


def _elapsed_ms() -> int:
    """Milliseconds of in-process hook work so far (see :data:`_T0`)."""
    return int((time.perf_counter() - _T0) * 1000)


def _ledger_key(surface: str, category: str, text: str) -> str:
    """Efficacy-ledger id for one emission, keyed on the text itself.

    The text is the one part of a firing that survives verbatim into the
    transcript, so keying on it lets the update-time classifier
    (:func:`repowise.core.sessions.efficacy.ledger_key`, which must stay
    identical to this) find the very row the hook wrote and settle whether the
    agent acted on it. Keying on the inputs instead would not work: the flood
    digest's input is the whole grep output, which the transcript does not
    keep. Doubling as the once-per-session dedup key is free — the same
    enrichment worded the same way has nothing new to say.
    """
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{surface}:{category}:{digest}"


def _extract_output_text(tool_output: object) -> str:
    """Pull the textual portion of a Claude Code tool_output, defensively.

    Claude Code's hook payload shape varies by tool: Bash/PowerShell
    surface ``stdout``/``stderr``, Grep surfaces a structured dict
    (``mode``/``content``/``filenames``), Glob a bare ``filenames`` list.
    We only need a string we can count newlines in, so we accept any of
    the shapes captured from real hook payloads.
    """
    if isinstance(tool_output, str):
        return tool_output
    if not isinstance(tool_output, dict):
        return ""
    for key in ("output", "result", "content", "stdout", "text"):
        val = tool_output.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list):
            # Some shapes wrap content as [{"type": "text", "text": "..."}].
            parts = []
            for item in val:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "\n".join(parts)
    # Grep files_with_matches / Glob: a list of paths, one per line.
    filenames = tool_output.get("filenames")
    if isinstance(filenames, list):
        parts = [f for f in filenames if isinstance(f, str)]
        if parts:
            return "\n".join(parts)
    return ""


def _relativize(file_path: str, repo_path: Path) -> str | None:
    """Repo-relative POSIX path for *file_path*, or None when outside it."""
    try:
        rel = Path(file_path).resolve().relative_to(Path(repo_path).resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()


@lru_cache(maxsize=8)
def _find_repo_root(cwd: Path) -> Path | None:
    """Walk up from cwd to find a directory with .repowise/.

    ``~/.repowise`` is the user-level config dir and a ``.repowise`` at the
    system temp ROOT is always a stray artifact (a tool that indexed with
    cwd=$TMP), so neither counts as a repo opt-in; repos legitimately created
    UNDER either directory still match.

    Memoized: one hook invocation asks several times with the same cwd, and
    the walk costs up to 20 ``resolve()``/``is_dir()`` syscall pairs. The
    cache lives only as long as the hook process, so it cannot go stale.
    """
    current = Path(cwd).resolve()
    try:
        skip = {Path.home().resolve(), Path(tempfile.gettempdir()).resolve()}
    except (OSError, RuntimeError):
        skip = set()
    for _ in range(20):
        if current not in skip and (current / ".repowise").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
