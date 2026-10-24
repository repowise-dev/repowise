"""What git says about a change beyond its stored record: message body and
the symbol names in its diff's hunk headers.

Hunk headers are git's function-context lines (``@@ -1,2 +1,3 @@ def foo(``),
so the names are the enclosing definitions of the touched code as git's
funcname rules see them. Ceiling: a change touching more than
``MAX_DIFF_FILES`` files reads no symbols, since its diff (a vendored
dependency update) can run to hundreds of megabytes.

Trailers that name people (``Signed-off-by``, ``Co-authored-by``,
``Reviewed-By`` and any trailer line carrying an email) are stripped from the
body, so no person's name reaches the text index or its evidence.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from repowise.core.analysis import git_cli

MAX_DIFF_FILES = 100
_BATCH = 100
_HUNK_RE = re.compile(r"^@@ [^@]+ @@ ?(.*)$", re.MULTILINE)
_CALLABLE_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
_DEF_RE = re.compile(r"\b(?:class|struct|interface|enum|impl|trait|type|module)\s+([A-Za-z_]\w*)")
_PERSON_TRAILER_RE = re.compile(
    r"^[ \t]*(?:[A-Za-z][\w-]*-by|Cc|Author|Committer)[ \t]*:.*$"
    r"|^[ \t]*[A-Za-z][\w-]*[ \t]*:.*<[^<>\s]+@[^<>\s]+>.*$",
    re.IGNORECASE | re.MULTILINE,
)
#: ``(repo, head sha, symbols read) -> (body, symbols)``. A commit's text never
#: changes, so a long-lived MCP server reads each change once instead of
#: re-running ``git log -U0`` over the whole window on every suspects call.
#: Ceiling: LRU-bounded; one entry is a message body and a few names.
_TEXTS: OrderedDict[tuple[str, str, bool], tuple[str, tuple[str, ...]]] = OrderedDict()
_TEXTS_MAX = 8192
_TEXTS_LOCK = threading.Lock()


#: Results in flight are retained for waiters even if the LRU evicts them.
@dataclass
class _InFlightRead:
    event: threading.Event = field(default_factory=threading.Event)
    result: tuple[str, tuple[str, ...]] | None = None


_INFLIGHT: dict[tuple[str, str, bool], _InFlightRead] = {}
_WAIT_TIMEOUT_SECONDS = 120.0


def strip_person_trailers(body: str) -> str:
    """*body* without trailer lines that name a person."""
    return _PERSON_TRAILER_RE.sub("", body).strip()


def _git(repo: Path, *args: str) -> str | None:
    """Stdout, or ``None`` when git fails (one unknown sha fails the whole call)."""
    try:
        return git_cli._git(list(args), str(repo))
    except (OSError, subprocess.SubprocessError):
        return None


def _log(repo: Path, args: list[str], shas: list[str]) -> str:
    """``git log`` over *shas* in batches; a failed batch is retried sha by sha."""
    parts: list[str] = []
    for i in range(0, len(shas), _BATCH):
        batch = shas[i : i + _BATCH]
        raw = _git(repo, "log", "--no-walk=unsorted", *args, *batch)
        if raw is None:
            raw = "".join(r for sha in batch if (r := _git(repo, "log", "--no-walk", *args, sha)))
        parts.append(raw)
    return "".join(parts)


def hunk_symbols(diff: str) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for context in _HUNK_RE.findall(diff):
        m = _CALLABLE_RE.search(context) or _DEF_RE.search(context)
        if m:
            names[m.group(1)] = None
    return tuple(names)


def read_change_texts(
    repo_path: Path | str, heads: Iterable[tuple[str, int]]
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``head_sha -> (body, symbols)`` for ``(head_sha, files_changed)`` pairs.

    A sha git cannot read is left out; the caller counts what is missing.
    """
    repo = Path(repo_path)
    heads = [(s.strip(), n) for s, n in heads if s and s.strip()]
    sizes = dict(heads)
    keys = {s: (str(repo), s, n <= MAX_DIFF_FILES) for s, n in sizes.items()}
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    claimed: list[tuple[str, int]] = []
    waits: dict[str, _InFlightRead] = {}
    with _TEXTS_LOCK:
        for sha, key in keys.items():
            if (hit := _TEXTS.get(key)) is not None:
                _TEXTS.move_to_end(key)
                out[sha] = hit
            elif (inflight := _INFLIGHT.get(key)) is not None:
                waits[sha] = inflight
            else:
                _INFLIGHT[key] = _InFlightRead()
                claimed.append((sha, sizes[sha]))
    if claimed:
        # Every claimed key is released even when the read raises, so a
        # waiter never blocks on an event nobody will set.
        read: dict[str, tuple[str, tuple[str, ...]]] = {}
        try:
            read = _read_change_texts(repo, claimed)
        finally:
            _release(keys, [s for s, _ in claimed], read)
        out.update(read)
    # Waiters retain the in-flight state, so cache eviction cannot
    # discard their results. A timeout falls back to a direct read.
    stale: list[tuple[str, int]] = []
    for sha, inflight in waits.items():
        if not inflight.event.wait(timeout=_WAIT_TIMEOUT_SECONDS):
            stale.append((sha, sizes[sha]))
        elif inflight.result is not None:
            out[sha] = inflight.result

    if stale:
        late = _read_change_texts(repo, [(s, n) for s, n in stale if s not in out])
        _store(keys, late)
        out.update(late)

    return out


def _store(
    keys: dict[str, tuple[str, str, bool]],
    texts: dict[str, tuple[str, tuple[str, ...]]],
) -> None:
    with _TEXTS_LOCK:
        for sha, text in texts.items():
            if sha in keys:
                _TEXTS[keys[sha]] = text
        while len(_TEXTS) > _TEXTS_MAX:
            _TEXTS.popitem(last=False)


def _release(
    keys: dict[str, tuple[str, str, bool]],
    shas: list[str],
    texts: dict[str, tuple[str, tuple[str, ...]]],
) -> None:
    """Cache results and wake waiters with their completed values."""
    with _TEXTS_LOCK:
        events = []
        for sha in shas:
            key = keys[sha]
            inflight = _INFLIGHT.pop(key, None)

            if inflight is not None:
                inflight.result = texts.get(sha)
                events.append(inflight.event)

            if sha in texts:
                _TEXTS[key] = texts[sha]

        while len(_TEXTS) > _TEXTS_MAX:
            _TEXTS.popitem(last=False)

    for event in events:
        event.set()


def _read_change_texts(
    repo: Path, heads: list[tuple[str, int]]
) -> dict[str, tuple[str, tuple[str, ...]]]:
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for rec in _log(repo, ["--format=%x00%H%n%b"], [s for s, _ in heads]).split("\x00"):
        sha, _, body = rec.partition("\n")
        if sha.strip():
            out[sha.strip()] = (strip_person_trailers(body), ())
    small = [s for s, n in heads if n <= MAX_DIFF_FILES and s in out]
    for rec in _log(repo, ["-m", "--first-parent", "-U0", "--format=%x00%H"], small).split("\x00"):
        sha, _, diff = rec.partition("\n")
        sha = sha.strip()
        if sha in out:
            out[sha] = (out[sha][0], hunk_symbols(diff))
    return out


__all__ = ["MAX_DIFF_FILES", "hunk_symbols", "read_change_texts", "strip_person_trailers"]
