"""Single-pass repo-wide commit index for git_indexer.

The original per-file path in ``git_indexer._index_file`` spawned one
``git log --numstat`` subprocess per tracked file. On a 5,000-file repo
that meant 5,000 process spawns — ~50-100 ms each on Windows — which
made the git phase dominate the total ``repowise init`` wall-clock.

This module replaces the fan-out with one repo-wide ``git log`` pass
and an in-memory bucketing step. The shape mirrors what
``_compute_co_changes`` already does — one subprocess, fan-out via
Python dicts — so any future debugging only has one log format to
understand.

The batched path is only used when ``follow_renames=False`` (the
default). Rename-tracking still falls back to the per-file ``--follow``
path because git's rename heuristics are evaluated against a single
input file, not retro-fittable from a repo-wide log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .git_indexer import _CommitRec

logger = structlog.get_logger(__name__)


def load_git_ai_note_agents(repo: object, commit_limit: int) -> dict[str, str]:
    """Map ``commit_sha → agent`` from git-ai authorship notes (``refs/notes/ai``).

    Returns an empty dict when the ref is absent — the common case, gated by a
    single cheap ``for-each-ref`` so 99.9% of repos pay nothing and add no git
    pass. Only repos actually using git-ai incur the one bounded
    ``git log --notes=ai`` walk here, whose ``sha → agent`` result the commit
    walk then reads by-key (no re-parse per touched file). Any failure returns
    ``{}`` — a notes read must never break the git index. See the git-ai
    standard v3.0.0 for the note format.
    """
    from .git_indexer import _FIELD_SEP, _RECORD_SEP
    from .git_indexer.agent_provenance import _agent_from_git_ai_note

    try:
        if not repo.git.for_each_ref("refs/notes/ai"):  # type: ignore[attr-defined]
            return {}
    except Exception:
        return {}

    try:
        # ``%N`` is the note body; the leading ``%x00``/``%x1f`` mirror the main
        # walk's record/field separators so multi-line notes parse unambiguously.
        raw = repo.git.log(  # type: ignore[attr-defined]
            f"-{commit_limit}",
            "--no-merges",
            "--notes=ai",
            "--format=%x00%H%x1f%N",
        )
    except Exception as exc:
        logger.warning("git_ai_notes_load_failed", error=str(exc))
        return {}

    agents: dict[str, str] = {}
    for chunk in raw.split(_RECORD_SEP):
        if not chunk.strip():
            continue
        sha, sep, note = chunk.partition(_FIELD_SEP)
        if not sep:
            continue
        agent = _agent_from_git_ai_note(note)
        if agent:
            agents[sha.strip()] = agent
    return agents


def load_commit_index(
    repo: object,
    commit_limit: int,
    indexable_files: set[str],
    *,
    commit_sink: list[dict] | None = None,
    since_ts: int | None = None,
    provenance_classifier: object | None = None,
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

    Failures (git unavailable, corrupt log output, etc.) return an
    empty dict so the caller can fall back to per-file indexing.
    """
    # Imported here to avoid a circular import — these live in git_indexer's
    # records module and this module is imported from there.
    from .git_indexer import (
        _LOG_FORMAT,
        _RECORD_SEP,
        _CommitRec,
        _extract_rename_paths,
        _parse_commit_record,
    )
    from .git_indexer.agent_provenance import AgentProvenanceClassifier

    if provenance_classifier is None:
        provenance_classifier = AgentProvenanceClassifier()

    try:
        raw = repo.git.log(  # type: ignore[attr-defined]
            f"-{commit_limit}",
            "--numstat",
            "--no-merges",
            f"--format={_LOG_FORMAT}",
        )
    except Exception as exc:
        logger.warning("repo_commit_index_failed", error=str(exc))
        return {}

    if not raw:
        return {}

    # git-ai authorship notes for this window (``{}`` unless the repo uses them).
    note_agents = load_git_ai_note_agents(repo, commit_limit)

    bucket: dict[str, list[_CommitRec]] = {}
    commits_parsed = 0

    # Split on the NUL record separator rather than newlines: commit bodies
    # (``%b``) are multi-line, so a line-based scan would mistake body lines
    # for numstat rows. The first chunk before the leading separator is empty.
    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        parsed = _parse_commit_record(record)
        if parsed is None:
            continue
        header, numstat_lines = parsed

        # Incremental bound: drop commits at/older than the newest already
        # persisted (newest-first walk, but author/commit-time can be slightly
        # out of order, so skip rather than break to stay correct).
        if since_ts is not None and header["ts"] <= since_ts:
            continue
        commits_parsed += 1

        # Agent provenance — classified ONCE per commit here (not per touched
        # file): the per-file records below share the result by reference.
        prov = provenance_classifier.classify(  # type: ignore[attr-defined]
            header["author_name"],
            header["author_email"],
            header["committer_name"],
            header["committer_email"],
            f"{header['subject']}\n{header['body']}",
            note_agent=note_agents.get(header["sha"]),
        )

        # Full change footprint of this commit (every file, not just the
        # indexable subset) — only accumulated when a sink is requested so the
        # default path pays nothing.
        commit_changes: list[tuple[str, int, int]] = [] if commit_sink is not None else None  # type: ignore[assignment]

        for line in numstat_lines:
            cols = line.split("\t")
            if len(cols) < 3:
                continue

            stat_path = cols[2]
            # Handle rename markers — ``{old => new}`` resolves to a new
            # path. Without ``--follow`` git still emits these for moves
            # detected via the rename heuristic; we add both names but
            # attribute the churn to the new path.
            if "=>" in stat_path:
                seen: set[str] = set()
                _old_path, new_path = _extract_rename_paths(stat_path, seen)
                target = new_path or stat_path
            else:
                target = stat_path

            try:
                added = int(cols[0]) if cols[0] != "-" else 0
                deleted = int(cols[1]) if cols[1] != "-" else 0
            except ValueError:
                added = 0
                deleted = 0

            if commit_changes is not None:
                commit_changes.append((target, added, deleted))

            if target not in indexable_files:
                continue

            # Each commit becomes one record per file it touched — the
            # per-file analyzer treats this list as the file's own history.
            bucket.setdefault(target, []).append(
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

        if commit_sink is not None:
            commit_sink.append(
                {
                    "sha": header["sha"],
                    "author_name": header["author_name"],
                    "author_email": header["author_email"],
                    "ts": header["ts"],
                    "subject": header["subject"],
                    "changes": commit_changes,
                    "agent_name": prov.agent,
                    "agent_autonomy_tier": prov.autonomy_tier,
                    "agent_channel": prov.channel,
                    "agent_confidence": prov.confidence,
                }
            )

    logger.debug(
        "repo_commit_index_built",
        commits_parsed=commits_parsed,
        files_with_history=len(bucket),
        indexable_files=len(indexable_files),
    )
    return bucket


def load_deep_commit_index(
    repo: object,
    per_file_limit: int,
    wanted_files: set[str],
    *,
    skip: int,
    deep_limit: int,
    provenance_classifier: object | None = None,
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

    The records differ from the per-file fallback in one documented way:
    churn comes from the repo-wide diff (rename rows attribute edit churn
    through the rename) rather than the pathspec-limited diff (which
    shows a rename as a whole-file addition), and simplification-rare
    merge commits never appear (``--no-merges``, like the window walk).
    That makes deep-bucketed files CONSISTENT with window-indexed files,
    which always had repo-walk semantics. Files absent from this bucket
    (pre-rename names the marker parser cannot resolve, or history deeper
    than *deep_limit*) keep the per-file fallback path.

    Failures return an empty dict; every missed file then falls back to
    the per-file path exactly as before.
    """
    from .git_indexer import (
        _LOG_FORMAT,
        _RECORD_SEP,
        _CommitRec,
        _extract_rename_paths,
        _parse_commit_record,
    )
    from .git_indexer.agent_provenance import AgentProvenanceClassifier

    if not wanted_files:
        return {}
    if provenance_classifier is None:
        provenance_classifier = AgentProvenanceClassifier()

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
        return {}

    # git-ai notes across the deep region (``{}`` unless the repo uses them).
    note_agents = load_git_ai_note_agents(repo, skip + deep_limit)

    bucket: dict[str, list[_CommitRec]] = {}
    commits_parsed = 0

    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        parsed = _parse_commit_record(record)
        if parsed is None:
            continue
        header, numstat_lines = parsed
        commits_parsed += 1

        # Classified lazily — only commits that actually touch a wanted
        # file pay the provenance regexes (the window walk classifies
        # every commit because the commit sink needs the labels).
        prov = None

        for line in numstat_lines:
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            stat_path = cols[2]
            if "=>" in stat_path:
                seen: set[str] = set()
                _old_path, new_path = _extract_rename_paths(stat_path, seen)
                target = new_path or stat_path
            else:
                target = stat_path

            if target not in wanted_files:
                continue
            records = bucket.setdefault(target, [])
            if len(records) >= per_file_limit:
                continue

            try:
                added = int(cols[0]) if cols[0] != "-" else 0
                deleted = int(cols[1]) if cols[1] != "-" else 0
            except ValueError:
                added = 0
                deleted = 0

            if prov is None:
                prov = provenance_classifier.classify(  # type: ignore[attr-defined]
                    header["author_name"],
                    header["author_email"],
                    header["committer_name"],
                    header["committer_email"],
                    f"{header['subject']}\n{header['body']}",
                    note_agent=note_agents.get(header["sha"]),
                )

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

    logger.debug(
        "deep_commit_index_built",
        commits_parsed=commits_parsed,
        files_bucketed=len(bucket),
        wanted_files=len(wanted_files),
        skip=skip,
    )
    return bucket
