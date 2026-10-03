"""Likely replacements for references the tree refutes.

A suggestion is evidence for the reader, never a verdict: it does not move a
finding's confidence, and nothing here rewrites a document. Every strategy
fires only on a unique answer, because a wrong "likely now" costs the reader
more than no suggestion at all.

Pure over :class:`~.resolver.RepoIndex` plus two injected callables, a rename
lookup and an on-disk probe, so a host without a checkout can run it. The one
I/O implementation, :func:`git_renames`, is a default the analyzer wires in
when it has a repository root.

A path is looked up both as written and joined relative to its document, and a
suggestion found through the relative form is written back in that form, so it
pastes into the same link.
"""

from __future__ import annotations

import os
import posixpath
import subprocess
from collections.abc import Callable, Mapping, Sequence
from difflib import SequenceMatcher, get_close_matches
from functools import cache
from pathlib import Path
from typing import Literal

from .constants import SuggestionBasis
from .models import DocReference, DriftKind
from .renderer import github_slug
from .resolver import RepoIndex, Resolution, anchor_host, join_relative

#: Where each given old repo path ended up, for as many of them as it knows.
RenameLookup = Callable[[Sequence[str]], Mapping[str, str]]

#: ``(suggestion, basis)``; both empty when no strategy found a unique answer.
Suggestion = tuple[str, SuggestionBasis | Literal[""]]

#: No suggestion: both parts empty.
NO_SUGGESTION: Suggestion = ("", "")
_PATH_KINDS = (DriftKind.PATH, DriftKind.LINK)

# difflib's ratio. 0.75 admits a typo or a one-word heading edit and rejects
# a different word of similar length.
_CLOSE_CUTOFF = 0.75
# Two candidates this close in ratio are a coin toss, not a suggestion.
_TIE_MARGIN = 0.05

_MAX_RENAME_PATHS = 200
_GIT_TIMEOUT_SECONDS = 3
# History searched for renames, bounded by a revision range (``-n`` counts
# matching commits, not scanned ones). Older renames go unsuggested.
_MAX_RENAME_COMMITS = 5000

#: ``(repo path, written relative to the document)`` for one reference.
_Form = tuple[str, bool]
_DirTails = Callable[[], Mapping[str, Sequence[str]]]


def suggest_all(
    misses: Sequence[Resolution],
    idx: RepoIndex,
    *,
    rename_lookup: RenameLookup | None = None,
    on_disk: Callable[[str], bool] | None = None,
) -> list[Suggestion]:
    """One suggestion per ``MISSING`` resolution, aligned with *misses*.

    Index-local strategies run first. The rename lookup is called at most once,
    batched over the path misses they left unanswered.
    """
    dir_tails = cache(lambda: _dirs_by_tail(idx.dirs))
    out = [_local(res, idx, dir_tails) for res in misses]
    if rename_lookup is None:
        return out

    pending = {
        i: _forms(res.ref.doc_path, res.ref.target)
        for i, res in enumerate(misses)
        if not out[i][0] and res.ref.kind in _PATH_KINDS
    }
    if not pending:
        return out
    wanted = sorted({path for forms in pending.values() for path, _ in forms})
    try:
        renames = rename_lookup(wanted)
    except Exception:  # an injected lookup must never fail the drift pass
        return out
    for i, forms in pending.items():
        for path, relative in forms:
            new = renames.get(path, "")
            if new and (new in idx.files or (on_disk is not None and on_disk(new))):
                out[i] = (_written(misses[i].ref.doc_path, new, relative), "git_rename")
                break
    return out


def apply_suggestion(
    line: str, ref: DocReference, suggestion: str
) -> tuple[str, tuple[tuple[int, int], ...]] | None:
    """*line* with *suggestion* in place, and each replaced span; ``None`` when it cannot be placed.

    Every standalone occurrence of the reference on the line is replaced, so the
    line never keeps a stale copy. A path or link suggestion replaces the target
    inside what was written, so a ``./`` prefix or a ``#fragment`` stays; other
    kinds replace the reference whole. Spans are 1-based and end-exclusive, in
    UTF-16 code units.
    """
    raw = ref.raw
    if not suggestion or ref.column < 0 or line[ref.column : ref.column + len(raw)] != raw:
        return None
    offset, old = 0, raw
    if ref.kind in _PATH_KINDS:
        offset, old = raw.find(ref.target), ref.target
        if offset < 0:
            return None
    starts = [at + offset for at in _occurrences(line, raw)]
    if ref.column + offset not in starts:
        return None
    new_line = line
    for start in reversed(starts):
        new_line = f"{new_line[:start]}{suggestion}{new_line[start + len(old):]}"
    spans = tuple(
        (_utf16_len(line[:start]) + 1, _utf16_len(line[: start + len(old)]) + 1)
        for start in starts
    )
    return new_line, spans


def _occurrences(line: str, raw: str) -> list[int]:
    """Where *raw* stands alone on *line*, not part of a longer name or path."""
    found, at = [], line.find(raw)
    while at >= 0:
        before = line[at - 1 : at]
        after = line[at + len(raw) : at + len(raw) + 1]
        if not _glued(before) and not _glued(after):
            found.append(at)
        at = line.find(raw, at + len(raw))
    return found


def _glued(char: str) -> bool:
    return bool(char) and (char.isalnum() or char in "_./-")


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def unique_close_match(word: str, candidates: frozenset[str] | set[str]) -> str:
    """The single close match for *word*, or ``""`` when none or a near tie."""
    matches = get_close_matches(word, sorted(candidates), n=2, cutoff=_CLOSE_CUTOFF)
    if not matches:
        return ""
    if len(matches) == 2:
        best, second = (SequenceMatcher(None, m, word).ratio() for m in matches)
        if best - second < _TIE_MARGIN:
            return ""
    return matches[0]


def _forms(doc: str, target: str) -> list[_Form]:
    """*target* as written, then joined relative to *doc* when that differs."""
    joined = join_relative(doc, target)
    return [(target, False)] if joined == target else [(target, False), (joined, True)]


def _written(doc: str, path: str, relative: bool) -> str:
    """*path* in the form the document used."""
    if not relative:
        return path
    return posixpath.relpath(path, doc.rsplit("/", 1)[0] if "/" in doc else ".")


def _dirs_by_tail(dirs: frozenset[str]) -> dict[str, list[str]]:
    """Directories of three or more segments, keyed by their last two."""
    out: dict[str, list[str]] = {}
    for d in dirs:
        parts = d.rsplit("/", 2)
        if len(parts) == 3:
            out.setdefault(f"{parts[1]}/{parts[2]}", []).append(d)
    return out


def _local(res: Resolution, idx: RepoIndex, dir_tails: _DirTails) -> Suggestion:
    kind = res.ref.kind
    if kind in _PATH_KINDS:
        return _package_split(res, idx, dir_tails)
    if kind is DriftKind.ANCHOR:
        return _similar_heading(res, idx)
    if kind is DriftKind.COMMAND:
        return _similar_target(res, idx)
    return NO_SUGGESTION


def _package_split(res: Resolution, idx: RepoIndex, dir_tails: _DirTails) -> Suggestion:
    """``a/b/x.py`` is gone and ``a/b/x/`` exists: the module became a package."""
    doc, target = res.ref.doc_path, res.ref.target
    base = target.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    if dot <= 0:
        return NO_SUGGESTION
    forms = _forms(doc, target[: len(target) - len(base) + dot])
    for stem, relative in forms:
        if stem in idx.dirs:
            return f"{_written(doc, stem, relative)}/", "package_split"
    # The package also moved (``core/ingestion/x.py`` -> ``core/src/.../ingestion/x/``):
    # accept the one directory under the same top level ending in the last two segments.
    for stem, relative in forms:
        segments = stem.split("/")
        if len(segments) < 3:
            continue
        top = segments[0] + "/"
        found = [d for d in dir_tails().get("/".join(segments[-2:]), ()) if d.startswith(top)]
        if len(found) == 1:
            return f"{_written(doc, found[0], relative)}/", "package_split"
    return NO_SUGGESTION


def _similar_heading(res: Resolution, idx: RepoIndex) -> Suggestion:
    ref = res.ref
    fragment = ref.target.partition("#")[2]
    anchors = idx.doc_anchors(anchor_host(idx, ref) or ref.doc_path)
    match = unique_close_match(github_slug(fragment), anchors)
    if not match:
        return NO_SUGGESTION
    return f"{ref.raw.partition('#')[0]}#{match}", "similar_heading"


def _similar_target(res: Resolution, idx: RepoIndex) -> Suggestion:
    ref = res.ref
    runner, _, name = ref.target.partition(":")
    candidates = idx.make_targets if runner == "make" else idx.npm_scripts
    match = unique_close_match(name, candidates)
    if not match or not ref.raw.endswith(name):
        return NO_SUGGESTION
    # The runner stays as written: ``pnpm run x`` is not rewritten to ``npm``.
    return f"{ref.raw[: len(ref.raw) - len(name)]}{match}", "similar_target"


# ---------------------------------------------------------------------------
# git history
# ---------------------------------------------------------------------------


def git_run(
    root: Path, *args: str, stdin: bytes | None = None, timeout: float = _GIT_TIMEOUT_SECONDS
) -> tuple[int, bytes] | None:
    """``(exit code, stdout)`` of one short git call, or ``None`` when it did not finish.

    Never fetches: in a blobless CI clone, rename detection would otherwise
    pull blobs over the network.
    """
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_LITERAL_PATHSPECS": "1"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotePath=false", *args],
            capture_output=True,
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return proc.returncode, proc.stdout


def _git_out(root: Path, *args: str) -> str:
    """stdout of one short git call, or ``""`` on any failure."""
    ran = git_run(root, *args)
    if ran is None or ran[0] != 0:
        return ""
    return ran[1].decode("utf-8", errors="replace")


def _pathspec_safe(path: str) -> bool:
    return bool(path) and not path.startswith("/") and ".." not in path.split("/")


def _commit_blocks(out: str) -> list[tuple[str, list[str]]]:
    """``--format=commit %H`` output as ``(sha, lines)`` in output order."""
    blocks: list[tuple[str, list[str]]] = []
    for line in out.splitlines():
        if line.startswith("commit ") and "\t" not in line:
            blocks.append((line[7:].strip(), []))
        elif line and blocks:
            blocks[-1][1].append(line)
    return blocks


def _deletions(root: Path, revs: Sequence[str], paths: Sequence[str]) -> dict[str, list[str]]:
    """Commits that deleted each of *paths*, newest first."""
    args = ["--no-renames", "--diff-filter=D", "--format=commit %H", "--name-only"]
    out = _git_out(root, "log", *args, *revs, "--", *paths)
    found: dict[str, list[str]] = {}
    for sha, lines in _commit_blocks(out):
        for path in lines:
            found.setdefault(path, []).append(sha)
    return found


def _renames_in(root: Path, shas: Sequence[str]) -> dict[str, dict[str, str]]:
    """``{sha: {old: new}}`` for the renames each of *shas* made."""
    out = _git_out(
        root, "show", "-M", "--diff-filter=R", "--name-status", "--format=commit %H", *shas
    )
    found: dict[str, dict[str, str]] = {}
    for sha, lines in _commit_blocks(out):
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].startswith("R"):
                found.setdefault(sha, {})[parts[1]] = parts[2]
    return found


def git_renames(root: Path, paths: Sequence[str]) -> dict[str, str]:
    """Where each of *paths* ended up through the renames in *root*'s history.

    Per hop, a pathspec-pruned ``git log`` finds the commit that deleted the
    path, then ``git show -M`` reads that commit alone for what it became. A
    pathspec cannot find the rename directly: git limits the diff to it before
    rename detection, so ``-- old.py`` sees only a deletion. A later hop counts
    only when its commit is newer than the one before, so a chain never joins
    an older, unrelated rename. Returns ``{}`` on any failure.
    """
    wanted = [p for p in dict.fromkeys(paths) if _pathspec_safe(p)][:_MAX_RENAME_PATHS]
    if not wanted:
        return {}
    boundary = _git_out(
        root, "rev-list", "--max-count=1", f"--skip={_MAX_RENAME_COMMITS}", "HEAD"
    ).strip()
    revs = [f"{boundary}..HEAD"] if boundary else ["HEAD"]

    order: dict[str, int] = {}

    def age(sha: str) -> int:
        """Position in the bounded history, 0 at HEAD; one rev-list, on demand."""
        if not order:
            order.update((s, i) for i, s in enumerate(_git_out(root, "rev-list", *revs).split()))
        return order.get(sha, len(order))

    final: dict[str, str] = {}
    seen = {p: {p} for p in wanted}
    # start -> (current path, age of the commit that created it; None at the start)
    active: dict[str, tuple[str, int | None]] = {p: (p, None) for p in wanted}
    while active:
        deletions = _deletions(root, revs, sorted({cur for cur, _ in active.values()}))
        chosen: dict[str, str] = {}
        for start, (cur, created) in active.items():
            shas = deletions.get(cur, [])
            if created is not None:
                # The first deletion after the path arrived, never an older one.
                shas = [s for s in shas if age(s) < created][-1:]
            if shas:
                chosen[start] = shas[0]
        if not chosen:
            break
        renames = _renames_in(root, sorted(set(chosen.values())))
        following: dict[str, tuple[str, int | None]] = {}
        for start, sha in chosen.items():
            new = renames.get(sha, {}).get(active[start][0])
            if not new or new in seen[start]:  # deleted outright, or a cycle
                continue
            seen[start].add(new)
            final[start] = new
            following[start] = (new, age(sha))
        active = following
    return final
