"""Canonical evidence identity and provenance for :mod:`tool_why` payloads.

Evidence identity is deliberately separate from decision identity.  A commit or
source range can support several real decisions, while one decision can cite
several commits.  The compact ``evidence_refs`` objects emitted here therefore
describe only the supporting evidence coordinates; decision ids, statuses and
lineage remain untouched.

The helpers do not merge persisted records or use text similarity. Evidence
objects are also written to the repo-local sidecar so another server worker can
resolve a just-emitted live or historical reference without scanning Git or
source files.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sqlite3
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from repowise.server.mcp_server._references import (
    _content_id,
    _path_identity,
    _reference,
)

ProvenanceKind = Literal[
    "human_decision",
    "extracted_rationale",
    "historical",
    "inferred",
    "unknown",
]

_HUMAN_SOURCES = frozenset({"adr", "cli", "session"})
_RATIONALE_SOURCES = frozenset(
    {"code_comment", "comment", "inline_marker", "rationale_comment"}
)
_HISTORICAL_SOURCES = frozenset(
    {"changelog", "commit", "git_archaeology", "pr", "readme_mining"}
)
_INFERRED_SOURCES = frozenset({"inferred", "semantic"})
_FULL_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_REFERENCE_CACHE_LIMIT = 4096
_reference_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_reference_cache_lock = threading.Lock()


def _remember_evidence_references(value: object) -> None:
    if isinstance(value, Mapping):
        identifier = value.get("id")
        repository = value.get("repository")
        if (
            isinstance(identifier, str)
            and identifier.startswith("ev_")
            and isinstance(repository, str)
        ):
            key = (repository, identifier)
            with _reference_cache_lock:
                _reference_cache[key] = copy.deepcopy(dict(value))
                _reference_cache.move_to_end(key)
                while len(_reference_cache) > _REFERENCE_CACHE_LIMIT:
                    _reference_cache.popitem(last=False)
        for child in value.values():
            _remember_evidence_references(child)
    elif isinstance(value, list):
        for child in value:
            _remember_evidence_references(child)


def resolve_cached_evidence_reference(
    repository: str, reference_id: str
) -> dict[str, Any] | None:
    """Return an exact evidence object emitted by this server process."""

    key = (repository, reference_id)
    with _reference_cache_lock:
        cached = _reference_cache.get(key)
        if cached is None:
            return None
        _reference_cache.move_to_end(key)
        return copy.deepcopy(cached)


def _persist_evidence_references(value: object, repo_root: str | Path) -> bool:
    from repowise.core.distill.store import OmissionStore

    references: dict[str, dict[str, Any]] = {}

    def collect(child: object) -> None:
        if isinstance(child, Mapping):
            identifier = child.get("id")
            repository = child.get("repository")
            if (
                isinstance(identifier, str)
                and identifier.startswith("ev_")
                and isinstance(repository, str)
            ):
                references[identifier] = dict(child)
            for nested in child.values():
                collect(nested)
        elif isinstance(child, list):
            for nested in child:
                collect(nested)

    collect(value)
    if not references:
        return True
    try:
        with OmissionStore.open_default(Path(repo_root)) as store:
            for identifier, reference_value in references.items():
                store.put_evidence_reference(
                    identifier,
                    json.dumps(reference_value, sort_keys=True, separators=(",", ":")),
                    repository=str(reference_value["repository"]),
                )
    except (OSError, sqlite3.Error):
        return False
    return True


def resolve_persisted_evidence_reference(
    repository: str, reference_id: str, repo_root: str | Path
) -> dict[str, Any] | None:
    """Resolve an exact evidence object emitted by this or another worker."""

    from repowise.core.distill.store import OmissionStore, default_store_path

    db_path = default_store_path(Path(repo_root))
    if not db_path.exists():
        return None
    try:
        with OmissionStore(db_path) as store:
            row = store.get_evidence_reference(reference_id)
    except (OSError, sqlite3.Error):
        return None
    if row is None or row["repository"] != repository:
        return None
    try:
        value = json.loads(row["content"])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def provenance_for_source(source: str | None) -> ProvenanceKind:
    """Translate a legacy extractor label into the public trust vocabulary."""

    normalized = (source or "").strip().lower()
    if normalized in _HUMAN_SOURCES:
        return "human_decision"
    if normalized in _RATIONALE_SOURCES:
        return "extracted_rationale"
    if normalized in _HISTORICAL_SOURCES:
        return "historical"
    if normalized in _INFERRED_SOURCES:
        return "inferred"
    return "unknown"


def _commit_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("commit", "sha", "subject", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _full_commit(
    value: object, resolved_commits: Mapping[str, str] | None = None
) -> str | None:
    candidate = _commit_value(value).lower()
    if _FULL_COMMIT_RE.fullmatch(candidate):
        return candidate
    if len(candidate) < 7 or not re.fullmatch(r"[0-9a-f]+", candidate):
        return None
    return (resolved_commits or {}).get(candidate)


def commit_reference(
    repository: str,
    commit: object,
    *,
    resolved_commits: Mapping[str, str] | None = None,
    fallback: object | None = None,
) -> dict[str, Any]:
    """Reference a full commit, or conservatively isolate incomplete history."""

    resolved = _full_commit(commit, resolved_commits)
    if resolved is not None:
        return _reference("commit", repository, commit=resolved)
    raw = _commit_value(commit)
    legacy = {"commit_prefix": raw, "context": fallback}
    return _reference(
        "legacy",
        repository,
        content_id=_content_id(legacy),
        **({"commit_prefix": raw} if raw else {}),
    )


def file_reference(
    repository: str,
    path: str,
    lines: Sequence[int] | None,
    *,
    fallback: object | None = None,
) -> dict[str, Any]:
    """Reference an exact source range; never equate it with the whole file."""

    normalized_path = _path_identity(path)
    if lines and len(lines) == 2 and all(isinstance(line, int) and line > 0 for line in lines):
        start, end = int(lines[0]), int(lines[1])
        if end >= start:
            return _reference(
                "file_range",
                repository,
                path=normalized_path,
                range=[start, end],
            )
    return _reference(
        "legacy",
        repository,
        path=normalized_path,
        content_id=_content_id({"path": normalized_path, "context": fallback}),
    )


def content_reference(
    repository: str,
    path: str,
    lines: Sequence[int] | None,
    content: str,
    *,
    fallback: object | None = None,
) -> dict[str, Any]:
    """Reference source text without manufacturing an unavailable end line."""

    normalized_path = _path_identity(path)
    normalized_content = " ".join(content.split())
    start = lines[0] if lines and isinstance(lines[0], int) and lines[0] > 0 else None
    if normalized_content and start is not None:
        coordinates: dict[str, Any] = {
            "path": normalized_path,
            "line": start,
            "content_id": _content_id(normalized_content),
        }
        ref = _reference("file_content", repository, **coordinates)
        if (
            len(lines or ()) == 2
            and isinstance(lines[1], int)
            and lines[1] >= start
        ):
            ref["range"] = [start, int(lines[1])]
        return ref
    return file_reference(repository, path, lines, fallback=fallback)


def _decision_commits(record: Any) -> list[object]:
    raw = getattr(record, "evidence_commits_json", None) or "[]"
    try:
        commits = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return list(commits) if isinstance(commits, list) else []


def decision_evidence_refs(
    record: Any,
    repository: str,
    *,
    resolved_commits: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return every piece of evidence cited by one decision record."""

    evidence_rows = list(getattr(record, "_why_evidence_rows", ()) or ())
    if not evidence_rows:
        evidence_rows = [record]
    refs: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        source = getattr(evidence, "source", None)
        commit = getattr(evidence, "evidence_commit", None)
        commits = [commit] if commit else _decision_commits(evidence)
        for commit in commits:
            ref = commit_reference(
                repository,
                commit,
                resolved_commits=resolved_commits,
                fallback={"evidence_id": getattr(evidence, "id", None)},
            )
            ref["provenance"] = "historical"
            ref["source"] = source
            ref["source_kind"] = source or "commit"
            ref["verification_basis"] = "historical"
            refs.append(ref)
        evidence_file = getattr(evidence, "evidence_file", None)
        if evidence_file:
            start = getattr(evidence, "evidence_line", None)
            quote = getattr(evidence, "source_quote", "") or ""
            verification = getattr(evidence, "verification", None)
            if quote and verification in (None, "exact"):
                ref = content_reference(
                    repository,
                    evidence_file,
                    [start, start] if start else None,
                    quote,
                    fallback={"evidence_id": getattr(evidence, "id", None)},
                )
            elif start and verification in (None, "exact"):
                ref = file_reference(
                    repository,
                    evidence_file,
                    [start, start] if start else None,
                    fallback={"evidence_id": getattr(evidence, "id", None)},
                )
            else:
                ref = _reference(
                    "legacy",
                    repository,
                    path=_path_identity(evidence_file),
                    content_id=_content_id(
                        {
                            "evidence_id": getattr(evidence, "id", None),
                            "source_quote": quote,
                        }
                    ),
                )
            ref["provenance"] = provenance_for_source(source)
            ref["source"] = source
            ref["source_kind"] = source or "unknown"
            ref["verification_basis"] = "indexed"
            if verification:
                ref["verification"] = verification
            refs.append(ref)
    if not refs:
        ref = _reference(
            "legacy",
            repository,
            content_id=_content_id({"decision_id": record.id}),
        )
        ref["provenance"] = provenance_for_source(getattr(record, "source", None))
        ref["source"] = getattr(record, "source", None)
        ref["source_kind"] = getattr(record, "source", None) or "unknown"
        ref["verification_basis"] = "indexed"
        refs.append(ref)
    unique: dict[tuple[str, object, object], dict[str, Any]] = {}
    for ref in refs:
        unique[(ref["id"], ref.get("provenance"), ref.get("source"))] = ref
    return sorted(
        unique.values(),
        key=lambda ref: (ref["id"], str(ref.get("provenance")), str(ref.get("source"))),
    )


def decision_collapse_key(record: Any) -> tuple[object, ...] | None:
    """Conservative local restatement key, independent of public evidence ids.

    The raw extractor source remains part of this compatibility merge.  All
    commits participate, and only full commit ids or exact source points are
    safe enough to collapse.  Missing ranges therefore retain both records.
    """

    source = (getattr(record, "source", None) or "").strip().lower()
    if getattr(record, "status", None) == "superseded" or getattr(
        record, "superseded_by", None
    ):
        return None
    commits = [_full_commit(commit) for commit in _decision_commits(record)]
    if commits and all(commits):
        return ("commits", source, *sorted(set(commits)))
    evidence_file = getattr(record, "evidence_file", None)
    evidence_line = getattr(record, "evidence_line", None)
    if evidence_file and isinstance(evidence_line, int) and evidence_line > 0:
        return ("file_point", source, _path_identity(evidence_file), evidence_line)
    return None


def _resolved_commits(
    result: object, records: Iterable[Any], repo_root: str | Path | None
) -> dict[str, str]:
    candidates: set[str] = set()
    for record in records:
        for value in _decision_commits(record):
            candidate = _commit_value(value).lower()
            if re.fullmatch(r"[0-9a-f]{7,64}", candidate):
                candidates.add(candidate)
        for evidence in getattr(record, "_why_evidence_rows", ()) or ():
            candidate = _commit_value(getattr(evidence, "evidence_commit", None)).lower()
            if re.fullmatch(r"[0-9a-f]{7,64}", candidate):
                candidates.add(candidate)

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"commit", "sha", "subject"}:
                    candidate = _commit_value(child).lower()
                    if re.fullmatch(r"[0-9a-f]{7,64}", candidate):
                        candidates.add(candidate)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    resolved = {candidate: candidate for candidate in candidates if _FULL_COMMIT_RE.fullmatch(candidate)}
    prefixes = sorted(candidates - resolved.keys())
    if not prefixes or repo_root is None:
        return resolved
    root = Path(repo_root)
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            cwd=str(root),
            input="".join(f"{prefix}^{{commit}}\n" for prefix in prefixes),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=None,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return resolved
    for prefix, line in zip(prefixes, proc.stdout.splitlines(), strict=False):
        parts = line.split()
        if len(parts) == 2 and parts[1] == "commit" and _FULL_COMMIT_RE.fullmatch(parts[0]):
            resolved[prefix] = parts[0].lower()
    return resolved


def _annotate_commit_row(
    row: dict[str, Any],
    repository: str,
    resolved_commits: Mapping[str, str],
    *,
    channel: str,
) -> None:
    commit = row.get("commit") or row.get("sha") or row.get("subject")
    row["provenance"] = "historical"
    ref = commit_reference(
            repository,
            commit,
            resolved_commits=resolved_commits,
            fallback={"channel": channel, "row": row},
        )
    ref["provenance"] = "historical"
    ref["source"] = channel
    ref["source_kind"] = channel
    ref["verification_basis"] = "historical"
    row["evidence_refs"] = [ref]


def _annotate_origin(
    origin: dict[str, Any],
    repository: str,
    resolved_commits: Mapping[str, str],
    records_by_id: Mapping[str, Any],
    records_by_title: Mapping[str, list[Any]],
) -> None:
    if origin.get("available"):
        origin["provenance"] = "historical"
    for row in origin.get("key_commits") or []:
        if isinstance(row, dict):
            _annotate_commit_row(row, repository, resolved_commits, channel="origin")
    for linked in origin.get("linked_decisions") or []:
        if not isinstance(linked, dict):
            continue
        linked["provenance"] = "inferred"
        candidates = records_by_title.get(str(linked.get("title") or ""), [])
        record = candidates[0] if len(candidates) == 1 else None
        if record is not None:
            linked["decision_id"] = record.id
            linked["decision_provenance"] = provenance_for_source(record.source)
        linked_refs: list[dict[str, Any]] = []
        for row in linked.get("evidence_commits") or []:
            if isinstance(row, dict):
                _annotate_commit_row(
                    row, repository, resolved_commits, channel="origin_link"
                )
                linked_refs.extend(row["evidence_refs"])
        if linked_refs:
            linked["evidence_refs"] = linked_refs


def annotate_response_evidence(
    result: dict[str, Any],
    repository: str,
    records: Iterable[Any] = (),
    *,
    resolved_commits: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Add self-resolving evidence refs and provenance to every ``get_why`` lane."""

    record_list = list(records)
    records_by_id = {record.id: record for record in record_list}
    records_by_title: dict[str, list[Any]] = {}
    for record in record_list:
        records_by_title.setdefault(record.title, []).append(record)
    resolved_commits = dict(resolved_commits or _resolved_commits(result, record_list, None))

    def annotate_decision(row: dict[str, Any]) -> None:
        record = records_by_id.get(str(row.get("id") or row.get("decision_id") or ""))
        if record is None:
            row.setdefault("provenance", "inferred" if row.get("snippet") else "unknown")
            return
        row["provenance"] = provenance_for_source(record.source)
        row["evidence_refs"] = decision_evidence_refs(
            record, repository, resolved_commits=resolved_commits
        )
        for lineage in row.get("lineage") or []:
            if not isinstance(lineage, dict):
                continue
            lineage_record = records_by_id.get(str(lineage.get("id") or ""))
            lineage["provenance"] = provenance_for_source(
                getattr(lineage_record, "source", None) or lineage.get("source")
            )
            if lineage.get("relation"):
                lineage["relation_provenance"] = "inferred"
            if lineage_record is not None:
                lineage["evidence_refs"] = decision_evidence_refs(
                    lineage_record, repository, resolved_commits=resolved_commits
                )

    for key in ("decisions", "stale_decisions", "proposed_awaiting_review"):
        for row in result.get(key) or []:
            if isinstance(row, dict):
                annotate_decision(row)

    origins: list[dict[str, Any]] = []
    origin_story = result.get("origin_story")
    if isinstance(origin_story, dict):
        origins.append(origin_story)
    for context in (result.get("target_context") or {}).values():
        if not isinstance(context, dict):
            continue
        for row in context.get("governing_decisions") or []:
            if isinstance(row, dict):
                candidates = records_by_title.get(str(row.get("title") or ""), [])
                if len(candidates) == 1:
                    row["id"] = candidates[0].id
                annotate_decision(row)
        origin = context.get("origin")
        if isinstance(origin, dict):
            origins.append(origin)
        archaeology = context.get("git_archaeology")
        if isinstance(archaeology, dict):
            _annotate_archaeology(archaeology, repository, resolved_commits)
    for origin in origins:
        _annotate_origin(
            origin, repository, resolved_commits, records_by_id, records_by_title
        )

    archaeology = result.get("git_archaeology")
    if isinstance(archaeology, dict):
        _annotate_archaeology(archaeology, repository, resolved_commits)
    for row in result.get("code_rationale") or []:
        if not isinstance(row, dict):
            continue
        row["provenance"] = "extracted_rationale"
        row["evidence_refs"] = [
            content_reference(
                repository,
                str(row.get("path") or ""),
                row.get("lines"),
                str(row.get("comment") or ""),
                fallback={"comment": row.get("comment")},
            )
        ]
        row["evidence_refs"][0]["provenance"] = "extracted_rationale"
        row["evidence_refs"][0]["source"] = "live_code_rationale"
        row["evidence_refs"][0]["source_kind"] = "code_comment"
        row["evidence_refs"][0]["verification"] = "exact"
        row["evidence_refs"][0]["verification_basis"] = "live"
    for row in result.get("episodes") or []:
        if isinstance(row, dict):
            _annotate_commit_row(row, repository, resolved_commits, channel="episode")
    _remember_evidence_references(result)
    if repo_root is not None:
        _persist_evidence_references(result, repo_root)
    return result


async def annotate_response_evidence_async(
    result: dict[str, Any],
    repository: str,
    records: Iterable[Any] = (),
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Resolve Git prefixes off the event loop, then annotate synchronously."""

    record_list = list(records)
    resolved = await asyncio.to_thread(
        _resolved_commits, result, record_list, repo_root
    )
    annotated = annotate_response_evidence(
        result,
        repository,
        record_list,
        resolved_commits=resolved,
    )
    persisted = await asyncio.to_thread(
        _persist_evidence_references, annotated, repo_root
    )
    if not persisted:
        annotated.setdefault("_meta", {})["reference_persistence"] = {
            "available": False,
            "reason": "repo-local evidence sidecar could not be written",
        }
    return annotated


def _annotate_archaeology(
    archaeology: dict[str, Any],
    repository: str,
    resolved_commits: Mapping[str, str],
) -> None:
    archaeology["provenance"] = "historical"
    for key in ("file_commits", "cross_references", "git_log"):
        for row in archaeology.get(key) or []:
            if isinstance(row, dict):
                _annotate_commit_row(
                    row, repository, resolved_commits, channel=f"archaeology:{key}"
                )
