"""Single-pass repo-wide commit index for git_indexer.

The original per-file path in ``git_indexer._index_file`` spawned one
``git log --numstat`` subprocess per tracked file. On a 5,000-file repo
that meant 5,000 process spawns — ~50-100 ms each on Windows — which
made the git phase dominate the total ``repowise init`` wall-clock.

This module replaces the fan-out with one repo-wide ``git log`` pass
and an in-memory bucketing step. The shape mirrors what
``compute_co_changes_and_entropy`` already does — one subprocess, fan-out via
Python dicts — so any future debugging only has one log format to
understand.

The batched path is only used when ``follow_renames=False`` (the
default). It still keeps a file's history across a rename: the walk reads
git's rename rows newest first and files older commits under the current
name (see :class:`~.git_indexer.records.RenameTrail`). ``follow_renames``
switches to the per-file ``--follow`` path instead.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .git_indexer import RenameTrail, _CommitRec
    from .git_indexer.agent_provenance import AgentProvenance

logger = structlog.get_logger(__name__)


@dataclass
class HistorySample:
    """One monotonic per-file history sample and its achieved coverage.

    Every eligible file keeps its newest
    ``min(per_file_limit, available_non_merge_history)`` commits. The recent and deep
    repo-wide walks are only optimisations for satisfying that contract;
    files they cannot prove complete use the per-file fallback.
    """

    commits: dict[str, list[_CommitRec]] = field(default_factory=dict)
    fallback_files: set[str] = field(default_factory=set)
    recent_files: set[str] = field(default_factory=set)
    deep_files: set[str] = field(default_factory=set)
    global_commits: int = 0
    deep_commits: int = 0
    history_complete_through_depth: int = 0


def _count_non_merge_commits(repo: object) -> int | None:
    """Return reachable non-merge commits, or ``None`` when unavailable."""
    try:
        return int(repo.git.rev_list("--count", "--no-merges", "HEAD").strip())  # type: ignore[attr-defined]
    except Exception:
        return None


def load_git_ai_note_agents(repo: object, commit_limit: int | None) -> dict[str, str]:
    """Map ``commit_sha → agent`` from git-ai authorship notes (``refs/notes/ai``).

    Returns an empty dict when the ref is absent — the common case, gated by a
    single cheap ``for-each-ref`` so 99.9% of repos pay nothing and add no git
    pass. Only repos actually using git-ai incur a ``git log --notes=ai`` walk
    here, whose ``sha → agent`` result the commit walk then reads by-key (no
    re-parse per touched file). The walk is bounded when *commit_limit* is an
    integer; ``None`` covers the complete history for the rare per-file
    fallback lane. Any failure returns ``{}`` — a notes read must never break
    the git index. See the git-ai standard v3.0.0 for the note format.
    """
    from .git_indexer import _FIELD_SEP, _RECORD_SEP
    from .git_indexer.agent_provenance import _agent_from_git_ai_note

    agents: dict[str, str] = {}
    for chunk in _git_ai_notes_log(repo, commit_limit).split(_RECORD_SEP):
        if not chunk.strip():
            continue
        sha, sep, note = chunk.partition(_FIELD_SEP)
        if not sep:
            continue
        agent = _agent_from_git_ai_note(note)
        if agent:
            agents[sha.strip()] = agent
    return agents


def _git_ai_notes_log(repo: object, commit_limit: int | None) -> str:
    """The raw ``refs/notes/ai`` log, or ``""`` when the ref is absent or git fails."""
    try:
        if not repo.git.for_each_ref("refs/notes/ai"):  # type: ignore[attr-defined]
            return ""
    except Exception:
        return ""

    try:
        # ``%N`` is the note body; the leading ``%x00``/``%x1f`` mirror the main
        # walk's record/field separators so multi-line notes parse unambiguously.
        args = ["--no-merges", "--notes=ai", "--format=%x00%H%x1f%N"]
        if commit_limit is not None:
            args.insert(0, f"-{commit_limit}")
        return repo.git.log(*args)  # type: ignore[attr-defined, no-any-return]
    except Exception as exc:
        logger.warning("git_ai_notes_load_failed", error=str(exc))
        return ""


#: Cache file name under the repository's ``.repowise`` directory, and the
#: shape version it carries. Bump the version when ``_LOG_FORMAT`` or the
#: record layout changes, so an older cache is re-walked rather than misread.
_WINDOW_CACHE_NAME = "commit_window_cache.json"
_WINDOW_CACHE_VERSION = 1


def _record_ts(record: str) -> int:
    """The committer timestamp of one raw record, or 0 when unparseable."""
    from .git_indexer import _FIELD_SEP

    parts = record.split(_FIELD_SEP, 6)
    try:
        return int(parts[5])
    except (IndexError, ValueError):
        return 0


def _window_records(repo: object, depth: int, cache_dir: Path | None) -> list[str]:
    """The raw log records of the newest ``depth`` non-merge commits.

    Git computes ``--numstat`` for every commit in the window, and on a
    repository with wide commits that walk costs seconds however few commits
    are new. With *cache_dir* the records from the last walk are kept beside
    the index, keyed by the HEAD they were taken at, and the next call asks git
    only for ``cached_head..HEAD``. The merged list is ordered the way the
    full walk orders it (committer time, newest first) and cut to ``depth``,
    so the parse below sees the same records a fresh walk would produce.

    A cached HEAD that is not an ancestor of the current one (a rebase, a
    reset) or a cache of another depth or version is discarded and the full
    walk runs. Every failure on the cache path falls back to the full walk;
    the cache can only ever save time, never change the answer.
    """
    if cache_dir is None:
        return _log_records(repo, f"-{depth}")

    cache_path = Path(cache_dir) / _WINDOW_CACHE_NAME
    try:
        head = repo.head.commit.hexsha  # type: ignore[attr-defined]
    except Exception:
        return _log_records(repo, f"-{depth}")

    try:
        records = _cached_window_records(repo, cache_path, head, depth)
    except Exception:
        records = None
    if records is None:
        records = _log_records(repo, f"-{depth}")
    _write_window_cache(cache_path, head, depth, records)
    return records


def _write_window_cache(cache_path: Path, head: str, depth: int, records: list[str]) -> None:
    try:
        from repowise.core.fsutils import atomic_write_text

        atomic_write_text(
            cache_path,
            json.dumps(
                {
                    "version": _WINDOW_CACHE_VERSION,
                    "head": head,
                    "depth": depth,
                    "records": records,
                }
            ),
        )
    except Exception as exc:
        logger.debug("commit_window_cache_write_failed", error=str(exc))


def _log_records(repo: object, revisions: str) -> list[str]:
    """The non-empty raw ``--numstat`` log records git returns for *revisions*."""
    from .git_indexer import _LOG_FORMAT, _RECORD_SEP

    raw = repo.git.log(  # type: ignore[attr-defined]
        revisions, "--numstat", "--no-merges", f"--format={_LOG_FORMAT}"
    )
    return [rec for rec in raw.split(_RECORD_SEP) if rec.strip()]


def _cached_window_records(
    repo: object, cache_path: Path, head: str, depth: int
) -> list[str] | None:
    """The cached window brought forward to *head*, or ``None`` when it cannot serve.

    Raises on an unreadable cache or a cached head that is not an ancestor of
    *head*; the caller treats both as a miss.
    """
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    if (
        cached.get("version") != _WINDOW_CACHE_VERSION
        or cached.get("depth") != depth
        or not isinstance(cached.get("records"), list)
    ):
        return None
    cached_head = str(cached.get("head") or "")
    if cached_head == head:
        return list(cached["records"])
    if not cached_head:
        return None
    # Raises when the cached head is unknown to this repository or
    # not behind HEAD, which is exactly when the cache is stale.
    repo.git.merge_base("--is-ancestor", cached_head, head)  # type: ignore[attr-defined]
    merged = _log_records(repo, f"{cached_head}..{head}") + list(cached["records"])
    # Stable, so a fresh record stays ahead of a cached one at the
    # same second, which is where the full walk puts it too.
    merged.sort(key=_record_ts, reverse=True)
    return merged[:depth]


def _report(stats_sink: dict[str, int | bool] | None, *, succeeded: bool, commits: int) -> None:
    if stats_sink is not None:
        stats_sink.update(succeeded=succeeded, commits=commits)


def _depth_newer_than(repo: object, commit_limit: int, since_ts: int) -> int | None:
    """How deep in the window the commits newer than *since_ts* reach.

    ``None`` when git fails. A ``%ct``-only pass is cheap next to the numstat
    walk, and both traverse the same revisions in the same order, so every
    commit past this depth is one the walk's ``ts <= since_ts`` check drops.
    """
    try:
        stamps = repo.git.log(  # type: ignore[attr-defined]
            f"-{commit_limit}", "--no-merges", "--format=%ct"
        ).split()
    except Exception as exc:
        logger.warning("repo_commit_index_failed", error=str(exc))
        return None
    return max(
        (position + 1 for position, stamp in enumerate(stamps) if _stamp_after(stamp, since_ts)),
        default=0,
    )


def _stamp_after(stamp: str, since_ts: int) -> bool:
    try:
        return int(stamp) > since_ts
    except ValueError:  # unparseable stamp: keep it in the window
        return True


def _numstat_counts(cols: list[str]) -> tuple[int, int]:
    """``(added, deleted)`` of one numstat row; binary (``-``) and garbled rows count 0."""
    try:
        added = int(cols[0]) if cols[0] != "-" else 0
        deleted = int(cols[1]) if cols[1] != "-" else 0
    except ValueError:
        return 0, 0
    return added, deleted


def _parse_numstat(
    numstat_lines: list[str],
) -> tuple[list[tuple[str, int, int]], list[tuple[str, str]]]:
    """A commit's ``(path, added, deleted)`` rows and its ``(old, new)`` renames.

    A rename row's churn goes to the new path.
    """
    from .git_indexer import _extract_rename_paths

    changes: list[tuple[str, int, int]] = []
    moved: list[tuple[str, str]] = []
    for line in numstat_lines:
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        target = cols[2]
        if "=>" in target:
            old_path, new_path = _extract_rename_paths(target, set())
            if old_path and new_path:
                moved.append((old_path, new_path))
            target = new_path or target
        changes.append((target, *_numstat_counts(cols)))
    return changes, moved


def _resolved(
    changes: list[tuple[str, int, int]], renames: RenameTrail | None
) -> list[tuple[str, int, int]]:
    """*changes* filed under each path's name at HEAD."""
    if renames is None:
        return changes
    return [(renames.resolve(target), added, deleted) for target, added, deleted in changes]


def _record_moves(renames: RenameTrail | None, moved: list[tuple[str, str]]) -> None:
    # Only after the commit's own paths are resolved (see RenameTrail).
    if renames is not None:
        for old_path, new_path in moved:
            renames.record(old_path, new_path)


class _CommitLabeler:
    """Labels each walked commit's agent provenance, once per commit.

    git-ai notes are read to *note_depth* (``{}`` unless the repo uses them);
    the map is read by sha, so a wider notes pass buys nothing. A caller may
    share its classifier and trace index so the trace file is read once per
    index. With *sink*, every labelled commit is also appended as a raw row.
    """

    def __init__(
        self,
        repo: object,
        note_depth: int,
        classifier: object | None,
        trace_index: object | None,
        sink: list[dict] | None = None,
    ) -> None:
        from .git_indexer.agent_provenance import AgentProvenanceClassifier, AgentTraceIndex

        self._note_agents = load_git_ai_note_agents(repo, note_depth)
        self._trace_index = trace_index if trace_index is not None else AgentTraceIndex.load(repo)
        self._classifier = classifier if classifier is not None else AgentProvenanceClassifier()
        self._sink = sink

    def label(self, header: dict, changes: list[tuple[str, int, int]]) -> AgentProvenance:
        # The agent-trace channel checks overlap with the commit's changed paths.
        trace_hit = (
            self._trace_index.resolve(  # type: ignore[attr-defined]
                header["sha"], header["parents"], {t for t, _, _ in changes}
            )
            if self._trace_index
            else None
        )
        prov: AgentProvenance = self._classifier.classify(  # type: ignore[attr-defined]
            header["author_name"],
            header["author_email"],
            header["committer_name"],
            header["committer_email"],
            f"{header['subject']}\n{header['body']}",
            note_agent=self._note_agents.get(header["sha"]),
            trace_agent=trace_hit[0] if trace_hit else None,
            trace_confidence=trace_hit[1] if trace_hit else "high",
        )
        if self._sink is not None:
            self._sink.append(_sink_row(header, changes, prov, trace_hit))
        return prov


def _sink_row(
    header: dict,
    changes: list[tuple[str, int, int]],
    prov: AgentProvenance,
    trace_hit: tuple[str, str, str | None] | None,
) -> dict:
    return {
        "sha": header["sha"],
        "author_name": header["author_name"],
        "author_email": header["author_email"],
        "ts": header["ts"],
        "tz_offset_minutes": header.get("tz_offset_minutes"),
        "subject": header["subject"],
        "changes": changes,
        "agent_name": prov.agent,
        "agent_autonomy_tier": prov.autonomy_tier,
        "agent_channel": prov.channel,
        "agent_confidence": prov.confidence,
        # Model id only when the trace channel actually won: a note
        # or service-identity match can outrank the trace above, and
        # only the trace record carries a model.
        "agent_model_id": (
            trace_hit[2] if (trace_hit and prov.channel == "agent_trace") else None
        ),
    }


class _FileBuckets:
    """Per-file commit records, newest first, for *files* only.

    With *cap*, a file stops collecting once it holds that many records.
    """

    def __init__(self, files: set[str], cap: int | None = None) -> None:
        self.files = files
        self.cap = cap
        self.records: dict[str, list[_CommitRec]] = {}

    def add(
        self, header: dict, prov: AgentProvenance, changes: list[tuple[str, int, int]]
    ) -> None:
        """File one commit under each of its changed paths that is tracked here."""
        from .git_indexer import _CommitRec

        for target, added, deleted in changes:
            if target not in self.files:
                continue
            records = self.records.setdefault(target, [])
            if self.cap is None or len(records) < self.cap:
                records.append(
                    _CommitRec(
                        sha=header["sha"],
                        author_name=header["author_name"],
                        author_email=header["author_email"],
                        ts=header["ts"],
                        is_merge=header["is_merge"],
                        subject=header["subject"],
                        body=header["body"],
                        added=added,
                        deleted=deleted,
                        agent=prov.agent,
                        agent_tier=prov.autonomy_tier,
                    )
                )


def load_commit_index(
    repo: object,
    commit_limit: int,
    indexable_files: set[str],
    *,
    commit_sink: list[dict] | None = None,
    since_ts: int | None = None,
    provenance_classifier: object | None = None,
    trace_index: object | None = None,
    cache_dir: Path | None = None,
    stats_sink: dict[str, int | bool] | None = None,
    renames: RenameTrail | None = None,
) -> dict[str, list[_CommitRec]]:
    """Bucket every commit in the recent history by the files it touched.

    *commit_limit* caps the depth (newest first); *indexable_files* is
    the allowlist of paths the caller will later read. Files outside
    this set are silently dropped — they are still seen by co-change
    detection upstream, but per-file metadata is only produced for the
    indexable set so there is no benefit to retaining their commits
    here.

    Returns a dict mapping ``file_path → [commit records, newest first]``.
    Files with no commits in the window simply aren't present in the
    dict; callers should treat ``KeyError`` / ``get(file, [])`` as
    "no recorded history" rather than an error.

    When *commit_sink* is supplied, each parsed commit is appended to it as a
    raw dict (``sha``, ``author_name``, ``author_email``, ``ts``, ``subject``,
    and ``changes`` — the full ``(path, added, deleted)`` list across *all*
    files in the commit, not just the indexable subset, so change diffusion is
    measured against the real footprint). This rides the same single walk —
    no extra git pass — and lets the caller build per-commit rows downstream
    (see :mod:`git_indexer.commit_rows`). The default (``None``) leaves the
    return value and behaviour unchanged.

    When *since_ts* is supplied (unix seconds), commits at or before it are
    skipped — used by the incremental path to capture only commits newer than
    the newest already-persisted one, so the commits surface stays current
    without re-walking the full window. Default (``None``) processes the whole
    depth.

    *provenance_classifier* (an
    :class:`~.git_indexer.agent_provenance.AgentProvenanceClassifier`) labels
    each commit's agent provenance once per commit — pure in-memory regex on
    the already-parsed record, no extra git pass. ``None`` uses the built-in
    pattern registry; callers with repo-local pattern extensions pass the
    config-aware instance instead.

    *cache_dir*, when given, keeps the window's raw records between runs so
    only the commits since the last walk are asked of git (see
    :func:`_window_records`). Ignored with *since_ts*, whose own bound already
    keeps that walk short.

    *renames*, when given, files each commit under the path its file has at
    HEAD and learns the window's renames, so a deeper walk can continue it.
    The commit sink keeps the paths as the commit wrote them.

    Failures (git unavailable, corrupt log output, etc.) return an
    empty dict so the caller can fall back to per-file indexing.
    """
    _report(stats_sink, succeeded=False, commits=0)

    # With *since_ts* only the prefix newer than it is asked of git; the
    # ``ts <= since_ts`` check in the walk stays, this only stops git from
    # diffing commits whose fate is already known.
    depth = commit_limit
    if since_ts is not None:
        newer = _depth_newer_than(repo, commit_limit, since_ts)
        if newer is None:
            return {}
        depth = newer
        if depth == 0:
            _report(stats_sink, succeeded=True, commits=0)
            logger.debug(
                "repo_commit_index_built",
                commits_parsed=0,
                files_with_history=0,
                indexable_files=len(indexable_files),
            )
            return {}

    try:
        records = _window_records(repo, depth, cache_dir if since_ts is None else None)
    except Exception as exc:
        logger.warning("repo_commit_index_failed", error=str(exc))
        return {}

    if not records:
        _report(stats_sink, succeeded=True, commits=0)
        return {}

    labeler = _CommitLabeler(repo, depth, provenance_classifier, trace_index, commit_sink)
    buckets = _FileBuckets(indexable_files)
    commits_parsed = 0
    for header, numstat_lines in _commits_after(records, since_ts):
        commits_parsed += 1
        # The full footprint (every file, not just the indexable subset): the
        # agent-trace overlap check and the commit sink both need it.
        commit_changes, moved = _parse_numstat(numstat_lines)
        prov = labeler.label(header, commit_changes)
        buckets.add(header, prov, _resolved(commit_changes, renames))
        _record_moves(renames, moved)

    logger.debug(
        "repo_commit_index_built",
        commits_parsed=commits_parsed,
        files_with_history=len(buckets.records),
        indexable_files=len(indexable_files),
    )
    _report(stats_sink, succeeded=True, commits=commits_parsed)
    return buckets.records


def _commits_after(records: list[str], since_ts: int | None) -> Iterator[tuple[dict, list[str]]]:
    """``(header, numstat_lines)`` of each parseable record newer than *since_ts*.

    Skips rather than stops at an older commit: the walk is newest first, but
    commit times can be slightly out of order.
    """
    from .git_indexer import _parse_commit_record

    for record in records:
        parsed = _parse_commit_record(record)
        if parsed is not None and (since_ts is None or parsed[0]["ts"] > since_ts):
            yield parsed


def load_deep_commit_index(
    repo: object,
    per_file_limit: int,
    wanted_files: set[str],
    *,
    skip: int,
    deep_limit: int,
    provenance_classifier: object | None = None,
    trace_index: object | None = None,
    stats_sink: dict[str, int | bool] | None = None,
    renames: RenameTrail | None = None,
) -> dict[str, list[_CommitRec]]:
    """Bucket commits OLDER than the recent window for *wanted_files* only.

    Walks ``git log --skip=<skip> -<deep_limit>`` — the region strictly
    beyond what :func:`load_commit_index` parsed (``--skip`` counts after
    ``--no-merges`` filtering, exactly like the window walk's ``-N`` cap,
    so the two regions partition the history with no gap or overlap).

    *wanted_files* is the set of indexable paths the window index missed;
    only their commits are retained, newest first, capped at
    *per_file_limit* per file — mirroring the per-file fallback's
    ``git log -<limit> -- <file>`` cap. One subprocess replaces one
    fallback spawn per missed file, which dominates the git phase on
    repos whose history is much deeper than the window (a 9k-commit
    monorepo left 3,295 of 4,857 files to the fallback).

    Churn comes from the repo-wide diff (rename rows attribute edit churn
    through the rename) rather than the pathspec-limited fallback diff (which
    shows a rename as a whole-file addition). All three rename-free lanes use
    ``--no-merges`` so changing lanes cannot change the retained commit set.
    That makes deep-bucketed files CONSISTENT with window-indexed files,
    which always had repo-walk semantics. *renames* continues the newer
    walks' rename trail, so a pre-rename commit here lands under the current
    name. Files absent from this bucket (history deeper than *deep_limit*)
    keep the per-file fallback path.

    Failures return an empty dict; every missed file then falls back to
    the per-file path exactly as before.
    """
    from .git_indexer import _LOG_FORMAT, _RECORD_SEP

    if not wanted_files:
        return {}
    _report(stats_sink, succeeded=False, commits=0)

    try:
        raw = repo.git.log(  # type: ignore[attr-defined]
            f"--skip={skip}",
            f"-{deep_limit}",
            "--numstat",
            "--no-merges",
            f"--format={_LOG_FORMAT}",
        )
    except Exception as exc:
        logger.warning("deep_commit_index_failed", error=str(exc))
        return {}

    if not raw:
        _report(stats_sink, succeeded=True, commits=0)
        return {}

    labeler = _CommitLabeler(repo, skip + deep_limit, provenance_classifier, trace_index)
    buckets = _FileBuckets(wanted_files, cap=per_file_limit)
    commits_parsed = 0
    records = [record for record in raw.split(_RECORD_SEP) if record.strip()]
    for header, numstat_lines in _commits_after(records, None):
        commits_parsed += 1
        changes, moved = _parse_numstat(numstat_lines)
        changes = _resolved(changes, renames)
        _record_moves(renames, moved)
        # Only commits touching a wanted file pay for provenance (the window
        # walk classifies every commit because the commit sink needs labels).
        if any(target in wanted_files for target, _, _ in changes):
            buckets.add(header, labeler.label(header, changes), changes)

    logger.debug(
        "deep_commit_index_built",
        commits_parsed=commits_parsed,
        files_bucketed=len(buckets.records),
        wanted_files=len(wanted_files),
        skip=skip,
    )
    _report(stats_sink, succeeded=True, commits=commits_parsed)
    return buckets.records


def _underfilled(sample: HistorySample, indexable_files: set[str], per_file_limit: int) -> set[str]:
    return {path for path in indexable_files if len(sample.commits.get(path, ())) < per_file_limit}


class _SharedWalks:
    """How far the recent and deep repo-wide walks have read into the history.

    Deep walks start below the recent window and step *step* commits at a
    time. Without a reliable history total one bounded deep walk is the safe
    limit, and unresolved files keep the per-file fallback.
    """

    def __init__(
        self,
        walk_deep: Callable[..., dict[str, list[_CommitRec]]],
        *,
        total_commits: int | None,
        recent_commits: int,
        start: int,
        step: int,
    ) -> None:
        self._walk_deep = walk_deep
        self._total = total_commits
        self._step = step
        self.cursor = start
        self.deep_walked = False
        self.recent_complete = total_commits is not None and total_commits <= recent_commits

    def has_more(self) -> bool:
        if self._total is None:
            return not self.deep_walked
        return self.cursor < self._total

    def walk_next(self, sample: HistorySample, per_file_limit: int, wanted: set[str]) -> bool:
        """Top up *sample* from the next deep region; ``False`` when that walk failed."""
        stats: dict[str, int | bool] = {}
        deep = self._walk_deep(wanted, skip=self.cursor, stats_sink=stats)
        for path, records in deep.items():
            remaining = per_file_limit - len(sample.commits.get(path, ()))
            if remaining > 0 and records:
                sample.commits.setdefault(path, []).extend(records[:remaining])
                sample.deep_files.add(path)
        if not stats.get("succeeded"):
            return False
        self.deep_walked = True
        sample.deep_commits += int(stats.get("commits", 0))
        self.cursor += self._step
        return True

    def reached_root(self) -> bool:
        if self.recent_complete:
            return True
        return self.deep_walked and self._total is not None and self.cursor >= self._total

    def complete_through(self, walked_depth: int) -> int:
        return walked_depth if self._total is None else min(self._total, walked_depth)


def _settle_fallback(
    sample: HistorySample, indexable_files: set[str], per_file_limit: int, reached_root: bool
) -> None:
    """Send files the shared walks left underfilled to the per-file walk.

    Not when those walks reached the repository root: then a short history is
    the whole history.
    """
    if not reached_root:
        # Ceiling: the per-file fallback log does not follow renames, so a
        # fallback file's history still starts at its last rename. Passing the
        # trail's old names as extra pathspecs, cut at the rename time, lifts it.
        sample.fallback_files = _underfilled(sample, indexable_files, per_file_limit)
        sample.deep_files.difference_update(sample.fallback_files)
    for path in indexable_files - sample.fallback_files:
        sample.commits.setdefault(path, [])


def load_sampled_commit_index(
    repo: object,
    per_file_limit: int,
    indexable_files: set[str],
    *,
    deep_limit: int,
    deep_threshold: int,
    commit_sink: list[dict] | None = None,
    provenance_classifier: object | None = None,
    trace_index: object | None = None,
    cache_dir: Path | None = None,
) -> HistorySample:
    """Resolve the recent, deep, and fallback sampling lanes once.

    Increasing ``per_file_limit`` can only add retained commits for a file.
    A recent-window hit never makes a file complete by itself: the deep walk
    tops it up, and a per-file walk remains required when the shared walks
    neither fill the cap nor reach the repository root.
    """
    from .git_indexer import RenameTrail

    # One trail across the recent and deep walks: they partition one
    # newest-first history, so a rename seen in the window applies below it.
    renames = RenameTrail()
    recent_stats: dict[str, int | bool] = {}
    recent = load_commit_index(
        repo,
        per_file_limit,
        indexable_files,
        commit_sink=commit_sink,
        provenance_classifier=provenance_classifier,
        trace_index=trace_index,
        cache_dir=cache_dir,
        stats_sink=recent_stats,
        renames=renames,
    )
    sample = HistorySample(
        commits={path: list(records[:per_file_limit]) for path, records in recent.items()},
        recent_files=set(recent),
        global_commits=int(recent_stats.get("commits", 0)),
    )
    if not recent_stats.get("succeeded"):
        sample.fallback_files = set(indexable_files)
        return sample

    walks = _SharedWalks(
        functools.partial(
            load_deep_commit_index,
            repo,
            per_file_limit,
            deep_limit=deep_limit,
            provenance_classifier=provenance_classifier,
            trace_index=trace_index,
            renames=renames,
        ),
        total_commits=_count_non_merge_commits(repo),
        recent_commits=sample.global_commits,
        start=per_file_limit,
        step=deep_limit,
    )
    underfilled = _underfilled(sample, indexable_files, per_file_limit)
    while not walks.recent_complete and len(underfilled) >= deep_threshold and walks.has_more():
        if not walks.walk_next(sample, per_file_limit, underfilled):
            break
        underfilled = _underfilled(sample, indexable_files, per_file_limit)

    sample.history_complete_through_depth = walks.complete_through(
        sample.global_commits + sample.deep_commits
    )
    _settle_fallback(sample, indexable_files, per_file_limit, walks.reached_root())
    return sample
