"""Read one evidence file, join it to the index and store the run.

The steps behind ``repowise evidence add``, without the command line, so a
server that receives the same file (a CI upload) runs the same checks and
stores the same run: :func:`ingest_evidence` takes the file's bytes, an open
session, the repository row and the checkout the clone lives in. A file or
option it will not take raises :class:`EvidenceRefusedError`; a run that reads but
does not map returns ``stored: false`` with a ``reason``, like the command.

The command runs the same steps in two parts (:func:`prepare_evidence` before
it opens the index, :func:`store_evidence` inside it) so a refused file never
opens a store.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.git_sha import is_full_sha

from .format_v1 import EVIDENCE_FORMAT, EvidenceDocument, EvidenceFormatError, parse_evidence
from .ingest import FLAG_WORKLOAD_CLASSES, EvidenceCaps, RunProvenance


class EvidenceRefusedError(Exception):
    """A file or option refused before anything is stored.

    *code* is ``file_too_large``, ``invalid_evidence`` or ``invalid_option``;
    *hint* says what to do; *option* names the ``evidence add`` option at
    fault for an ``invalid_option``.
    """

    def __init__(
        self, code: str, message: str, hint: str | None = None, *, option: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.option = option


def _bad_option(option: str, message: str) -> EvidenceRefusedError:
    return EvidenceRefusedError("invalid_option", message, option=option)


@dataclass(frozen=True)
class PreparedEvidence:
    """The file as read and checked: a v1 document (written or converted), or a profile run still to convert."""

    format: str
    #: The checkout the run used; frames are placed on its source.
    checkout_root: Path
    #: The commit the run measured.
    commit: str
    #: ``--value-kind`` as given: the clock of a profile still to convert.
    value_kind: str | None
    #: Whether the tree was dirty, as given or as the v1 file says; None when unknown.
    dirty: bool | None
    text: str | None = None
    doc: EvidenceDocument | None = None
    run: Any = None


@dataclass(frozen=True)
class StoredEvidence:
    """What :func:`store_evidence` did."""

    #: The result ``evidence add --format json`` prints.
    payload: dict[str, Any]
    #: Rows kept after the per-run cap (also when the run was restamped, writing none).
    rows_kept: int = 0


def check_options(workload: str, workload_class: str, environment: str, commit: str | None) -> None:
    """Refuse a usage error in the options before anything is read."""
    if workload_class == "production":
        raise _bad_option(
            "--class",
            "production is set only by a verified source (a request-log or "
            "statement ingest, a connector), never by a flag. Use entry_point for a "
            "profiled command or request replay, load_test for a load test.",
        )
    if workload_class not in FLAG_WORKLOAD_CLASSES:
        raise _bad_option(
            "--class", f"{workload_class!r} is not one of {', '.join(FLAG_WORKLOAD_CLASSES)}."
        )
    if not workload.strip():
        raise _bad_option("--workload", "name the workload that ran.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", environment):
        raise _bad_option("--environment", "one word of up to 32 letters, digits, '.', '_' or '-'.")
    if commit is not None and not is_full_sha(commit):
        raise _bad_option(
            "--commit", "the full 40- or 64-character SHA, e.g. $(git rev-parse HEAD)."
        )


def check_file_size(size: int, max_file_mb: float, name: str) -> None:
    """Refuse a file of *size* bytes over ``evidence.max_file_mb``."""
    if size > max_file_mb * 1024 * 1024:
        raise EvidenceRefusedError(
            "file_too_large",
            f"{name} is {size / 1048576:.1f} MB, over evidence.max_file_mb ({max_file_mb:g}).",
            f"Run `repowise config set evidence.max_file_mb {math.ceil(size / 1048576)}`, "
            "then add the file again, or ingest a smaller run.",
        )


def evidence_caps(config: Any) -> tuple[float, EvidenceCaps, list[str]]:
    """``evidence.max_file_mb``, the store caps, and the settings ignored as invalid, from *config*."""
    from repowise.core.settings import resolve

    problems: list[str] = []

    def value(key: str) -> Any:
        resolved = resolve(f"evidence.{key}", config)
        if resolved.problem:
            problems.append(resolved.problem)
        return resolved.value

    max_file_mb = value("max_file_mb")
    caps = EvidenceCaps(
        max_symbols_per_run=value("max_symbols_per_run"),
        max_rows_per_run=value("max_rows_per_run"),
        history_runs=value("history_runs"),
        max_runs=value("max_runs"),
    )
    return max_file_mb, caps, problems


def input_format(given: str | None, data: bytes) -> str:
    """*given* checked against the registry, else the format sniffed from *data*.

    A file no profile format claims is read as v1, whose parser says what is
    wrong with it.
    """
    from .profile_formats import SNIFF_BYTES, detect_profile_format, profile_formats

    names = [EVIDENCE_FORMAT, *(name for name, _ in profile_formats.all())]
    if given is not None:
        if given not in names:
            raise _bad_option("--input-format", f"{given!r} is not one of {', '.join(names)}.")
        return given
    head = data[:SNIFF_BYTES]
    if EVIDENCE_FORMAT.encode() in head:
        return EVIDENCE_FORMAT
    return detect_profile_format(head) or EVIDENCE_FORMAT


def prepare_evidence(
    data: bytes,
    *,
    name: str,
    checkout_root: Path,
    commit: str | None = None,
    dirty: bool | None = None,
    input_format_name: str | None = None,
    value_kind: str | None = None,
    max_file_mb: float | None = None,
) -> PreparedEvidence:
    """*data* read as v1 or a registered format, with its clock and commit checked.

    *name* names the file in messages. *max_file_mb*, when given, refuses a
    larger file. Raises :class:`EvidenceRefusedError`.
    """
    from .profile_convert import refuse_typescript
    from .profile_formats import profile_formats

    if max_file_mb is not None:
        check_file_size(len(data), max_file_mb, name)
    fmt = input_format(input_format_name, data)
    text: str | None = None
    doc: EvidenceDocument | None = None
    run: Any = None
    try:
        if not data.strip():
            # Every reader would name a format the file lacks; the run that writes it never finished.
            raise EvidenceFormatError(
                f"{name} is empty: the profiled run probably crashed or stopped before writing it",
                "run the command again, check its output for a crash, then ingest the file it writes",
            )
        if fmt == EVIDENCE_FORMAT:
            text = data.decode("utf-8", errors="replace")
            doc = parse_evidence(text)
        else:
            reader = profile_formats.get(fmt)
            if reader.to_v1 is not None:
                text = json.dumps(reader.to_v1(data), sort_keys=True)
                doc = parse_evidence(text)
            else:
                run = reader.read(data)
                refuse_typescript(run, str(checkout_root))
    except EvidenceFormatError as exc:
        raise EvidenceRefusedError("invalid_evidence", str(exc), exc.hint) from None
    _check_value_kind(fmt, doc, run, value_kind)
    return PreparedEvidence(
        format=fmt,
        checkout_root=checkout_root,
        commit=_measured_commit(commit, fmt, doc),
        value_kind=value_kind,
        dirty=dirty if dirty is not None or doc is None else doc.dirty,
        text=text,
        doc=doc,
        run=run,
    )


def _check_value_kind(
    fmt: str, doc: EvidenceDocument | None, run: Any, value_kind: str | None
) -> None:
    """A converted profile needs its clock; a v1 file's own must not be contradicted."""
    if doc is not None:
        if value_kind is not None and doc.value_kind is None:
            raise _bad_option(
                "--value-kind",
                f"a {doc.kind} file records no clock; --value-kind names the clock "
                "of a profile only, so leave it out.",
            )
        if value_kind is not None and doc.value_kind != value_kind:
            raise _bad_option(
                "--value-kind",
                f"the file's value_kind is {doc.value_kind}; --value-kind only names "
                "the clock of a converted profile, so pass the same or none.",
            )
        return
    if value_kind is None and run.value_kind is None:
        raise _bad_option(
            "--value-kind",
            f"a {fmt} profile does not say which clock it sampled: pass "
            "--value-kind cpu for on-CPU sampling, wall for wall-clock time.",
        )


def _measured_commit(commit: str | None, fmt: str, doc: EvidenceDocument | None) -> str:
    """The commit the run measured: *commit* or the file's, which must agree.

    A converted file's commit is what its tool recorded, never taken on its
    own: *commit* is required and the recorded one only cross-checks it.
    """
    in_file = doc.commit_sha if doc is not None else None
    converted = fmt != EVIDENCE_FORMAT
    if commit is not None and in_file is not None and commit != in_file:
        raise _bad_option(
            "--commit",
            f"the {fmt} file records commit {in_file}, so it did not measure "
            f"{commit}; pass the commit the run measured."
            if converted
            else f"the file says it measured {in_file}; pass the same commit or none.",
        )
    measured = commit or (None if converted else in_file)
    if measured is None:
        raise _bad_option(
            "--commit",
            "say which commit was measured: --commit $(git rev-parse HEAD) at the time "
            "of the run" + ("." if converted else ", or commit_sha in the file."),
        )
    return measured


async def store_evidence(
    session: Any,
    repo: Any,
    prepared: PreparedEvidence,
    *,
    repo_path: Path,
    workload: str,
    workload_class: str,
    environment: str = "unknown",
    allow_partial: bool = False,
    caps: EvidenceCaps,
    source: str = "cli",
) -> StoredEvidence:
    """Join *prepared* to the index of *repo* (a repository row) and store the run.

    *repo_path* is the indexed checkout: its HEAD says whether the run is
    stale, and its settings rank the refreshed read model.
    """
    from repowise.core.analysis.health.coverage import live_head_commit
    from repowise.core.analysis.health.coverage.discovery import mostly_unmapped
    from repowise.core.persistence.crud import save_evidence_run

    from .hotness import refresh_symbol_hotness
    from .ingest import (
        cap_rows,
        entry_paths,
        join_entries,
        load_origins,
        load_symbols,
        run_fields,
        run_key,
        stamp_bodies,
        unlisted_count,
    )

    root = prepared.checkout_root
    index_commit = getattr(repo, "head_commit", None)
    head_sha = live_head_commit(repo_path, repo)
    changed, dirty = _tree_state(root, prepared.commit, prepared.dirty)

    text, doc, conversion = prepared.text, prepared.doc, None
    if prepared.run is not None:
        conversion = await _convert(
            session, repo.id, prepared.run, root, changed, prepared.value_kind
        )
        text = json.dumps(conversion.document, sort_keys=True)
        doc = parse_evidence(text)

    paths = entry_paths(doc)
    origins = await load_origins(session, repo.id, paths)
    joined = join_entries(
        doc,
        await load_symbols(session, repo.id, paths),
        origins,
        changed or frozenset(),
    )
    # A benchmark or test keeps its row without its function, so only the
    # kinds that need the join can be mostly unmapped.
    partial = doc.kind in ("profile", "query_count") and mostly_unmapped(
        joined.matched, len(doc.entries)
    )
    provenance = RunProvenance(
        workload_name=workload,
        workload_class=workload_class,
        commit_sha=prepared.commit,
        checkout_root=str(root),
        dirty=dirty,
        environment=environment,
        source=source,
        tests_failed=prepared.run.tests_failed if prepared.run is not None else None,
    )
    summary = _summary(doc, provenance, joined, partial, index_commit, head_sha)
    summary["input_format"] = prepared.format
    if conversion is not None:
        summary["conversion"] = conversion.summary()
    if partial and not allow_partial:
        return StoredEvidence({**summary, "stored": False, "reason": "mostly_unmapped"})
    if not joined.rows:
        return StoredEvidence({**summary, "stored": False, "reason": "nothing_matched"})

    rows, truncated = cap_rows(doc.kind, joined.rows, caps)
    if doc.kind == "profile":
        await stamp_bodies(
            session,
            repo.id,
            rows,
            index_at_measured_commit=index_commit is not None and index_commit == prepared.commit,
        )
    write = await save_evidence_run(
        session,
        repo.id,
        run_fields(
            doc,
            provenance,
            joined,
            key=run_key(text, provenance, doc.kind, index_commit),
            truncated=truncated,
            mapping_partial=partial,
            unlisted=unlisted_count(joined.rows, origins) if doc.kind == "profile" else None,
        ),
        rows,
        history_runs=caps.history_runs,
        max_runs=caps.max_runs,
    )
    if doc.kind == "profile":
        # Profiles are what the read model is built from, and the save may
        # have pruned an older one. Best effort: the run stays stored.
        await refresh_symbol_hotness(
            session, repo.id, head_commit=index_commit, repo_path=repo_path
        )
    payload = {
        **summary,
        "stored": True,
        "run_id": write.run_id,
        "restamped": write.restamped,
        "rows_written": write.rows_written,
        "truncated": truncated,
        "runs_pruned": write.runs_pruned,
    }
    return StoredEvidence(payload, rows_kept=len(rows))


async def ingest_evidence(
    session: Any,
    repo: Any,
    data: bytes,
    *,
    repo_path: Path,
    workload: str,
    workload_class: str,
    name: str = "the evidence file",
    checkout_root: Path | None = None,
    commit: str | None = None,
    dirty: bool | None = None,
    environment: str = "unknown",
    input_format_name: str | None = None,
    value_kind: str | None = None,
    allow_partial: bool = False,
    max_file_mb: float | None = None,
    caps: EvidenceCaps | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    """Read *data*, join it to the index of *repo* and store the run; what ``evidence add`` does.

    *repo* is the repository row, *repo_path* the checkout its index was
    built from; *checkout_root* is the checkout the run used (default
    *repo_path*), whose source places a profile's frames. The other options
    are ``evidence add``'s; *max_file_mb* and *caps* default to the
    repository's ``evidence`` settings. Returns the result ``evidence add
    --format json`` prints. Raises :class:`EvidenceRefusedError` for a file or
    option it will not take.
    """
    commit = commit.lower() if commit is not None else None
    check_options(workload, workload_class, environment, commit)
    if max_file_mb is None or caps is None:
        from repowise.core.repo_config import load_repo_config

        default_mb, default_caps, _ = evidence_caps(load_repo_config(repo_path))
        max_file_mb = default_mb if max_file_mb is None else max_file_mb
        caps = default_caps if caps is None else caps
    prepared = prepare_evidence(
        data,
        name=name,
        checkout_root=(checkout_root or repo_path).resolve(),
        commit=commit,
        dirty=dirty,
        input_format_name=input_format_name,
        value_kind=value_kind,
        max_file_mb=max_file_mb,
    )
    stored = await store_evidence(
        session,
        repo,
        prepared,
        repo_path=repo_path,
        workload=workload.strip(),
        workload_class=workload_class,
        environment=environment,
        allow_partial=allow_partial,
        caps=caps,
        source=source,
    )
    return stored.payload


async def _convert(
    session: Any,
    repo_id: str,
    run: Any,
    root: Path,
    changed: frozenset[str] | None,
    value_kind: str | None,
) -> Any:
    """*run*'s frames joined to the index at *root*, as a v1 document (or a refusal)."""
    from repowise.core.frame_resolver import checkout_reader, frame_paths, load_frame_symbols

    from .profile_convert import convert_profile

    symbols, origins = await load_frame_symbols(
        session,
        repo_id,
        frame_paths(run.frames, root=str(root)),
        fold_case=sys.platform == "win32",
    )
    return convert_profile(
        run,
        root=str(root),
        symbols_by_file=symbols,
        dirty_paths=changed or (),
        origins=origins,
        read_lines=checkout_reader(root),
        value_kind=value_kind,
    )


def _tree_state(root: Path, commit: str, dirty: bool | None) -> tuple[Any, bool | None]:
    """Files that differ from *commit*, and *dirty* filled in from them.

    Files that differ from the commit are skipped: their lines are not the
    ones measured. Known only when the commit is the checkout's HEAD and git
    can say; otherwise the tree's state stays unknown.
    """
    from repowise.core.analysis.health.coverage import live_head_commit

    from .ingest import uncommitted_paths

    at_head = live_head_commit(root, None) == commit
    changed = uncommitted_paths(root) if at_head else None
    if dirty is None and changed is not None:
        dirty = bool(changed)
    return changed, dirty


def _summary(
    doc: Any,
    provenance: RunProvenance,
    joined: Any,
    partial: bool,
    index_commit: str | None,
    head_sha: str | None,
) -> dict[str, Any]:
    """What the run held and how it mapped, stored or not."""
    from collections import Counter

    commit = provenance.commit_sha
    return {
        "kind": doc.kind,
        "workload": provenance.workload_name,
        "class": provenance.workload_class,
        "commit_sha": commit,
        "index_commit": index_commit,
        "stale": head_sha is not None and commit != head_sha,
        "dirty": provenance.dirty,
        "entries": len(doc.entries),
        "matched": joined.matched,
        "skipped": dict(sorted((Counter(doc.skipped) + joined.skipped).items())),
        "unlinked": dict(sorted(joined.unlinked.items())),
        "mapping_partial": partial,
        "unmatched_sample": joined.unmatched_sample,
        "tests_failed": provenance.tests_failed,
    }


__all__ = [
    "EvidenceRefusedError",
    "PreparedEvidence",
    "StoredEvidence",
    "check_file_size",
    "check_options",
    "evidence_caps",
    "ingest_evidence",
    "input_format",
    "prepare_evidence",
    "store_evidence",
]
