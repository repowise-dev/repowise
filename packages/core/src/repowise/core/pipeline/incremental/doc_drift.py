"""Stored document dependencies used to scope an incremental drift pass."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.doc_drift.constants import MANIFEST_NAMES, MAX_DOC_BYTES
from repowise.core.analysis.doc_drift.extractor import extract, is_checkable_document
from repowise.core.analysis.doc_drift.models import DriftKind
from repowise.core.analysis.doc_drift.renderer import GITHUB, RendererMap
from repowise.core.analysis.doc_drift.resolver import join_relative

from ._common import LogFn, _noop_log
from .analysis import DocDriftUpdate


async def load_doc_drift_documents(
    repo_path: Any,
    update: DocDriftUpdate,
    source_map: dict[str, bytes] | None,
    tracked_paths: set[str] | frozenset[str],
    *,
    log: LogFn | None = None,
) -> frozenset[str] | None:
    """Changed documents and documents naming changed targets; ``None`` rechecks all.

    A manifest change or unavailable dependency history needs the full pass.
    Symbol-name dependencies are added by the analyzer from the current diff.
    """
    paths = frozenset(update.changed_paths)
    if any(path.rsplit("/", 1)[-1] in MANIFEST_NAMES for path in paths):
        return None
    if RendererMap.detect(paths).summary() != GITHUB or _resolution_rules_changed(update, tracked_paths):
        return None
    from ...persistence import create_engine, create_session_factory, get_session
    from ...persistence.crud import (
        get_doc_drift_findings,
        get_doc_drift_references,
        get_repository_by_path,
    )
    from ...persistence.database import resolve_db_url

    try:
        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                findings = await get_doc_drift_findings(session, repo.id)
                references = await get_doc_drift_references(session, repo.id)
                if not findings and not references:
                    return None
                documents = _stored_documents(findings, references, paths)
                return frozenset(documents | _newly_affected_documents(update, source_map or {}))
        finally:
            await engine.dispose()
    except Exception as exc:
        (log or _noop_log)(f"[yellow]Stored doc drift unavailable; rechecking all documents: {exc}[/yellow]")
        return None


def _stored_documents(findings: list[Any], references: list[Any], paths: frozenset[str]) -> set[str]:
    """Changed documents and documents whose stored targets or suggestions moved."""
    documents = {path for path in paths if is_checkable_document(path)}
    documents.update(
        row.document_path for row in references
        if _target_changed(row.document_path, row.target_path, paths)
    )
    documents.update(
        row.file_path for row in findings
        if _target_changed(row.file_path, row.target, paths)
        or _target_changed(row.file_path, row.suggestion or "", paths)
    )
    return documents


def _target_changed(document: str, target: str, paths: frozenset[str]) -> bool:
    """Include relative links, basename matches, directories and package splits."""
    target = target.partition("#")[0].rstrip("/")
    if not target:
        return False
    candidates = {target, join_relative(document, target)}
    stem = PurePosixPath(target).stem
    return any(
        path == candidate or path.startswith(candidate + "/")
        or path.rsplit("/", 1)[-1] == candidate.rsplit("/", 1)[-1]
        or stem in PurePosixPath(path).parts[:-1]
        for path in paths for candidate in candidates
    )


def _resolution_rules_changed(update: DocDriftUpdate, tracked: set[str] | frozenset[str]) -> bool:
    """New top-level directories or indexed suffixes can change any path verdict."""
    before = (set(tracked) - update.added_paths) | update.removed_paths

    def rules(paths: set[str] | frozenset[str]) -> tuple[set[str], set[str]]:
        return (
            {p.split("/", 1)[0] for p in paths if "/" in p},
            {PurePosixPath(p).suffix.lower() for p in paths},
        )

    return rules(before) != rules(tracked)


def _newly_affected_documents(update: DocDriftUpdate, sources: dict[str, bytes]) -> set[str]:
    """Recheck ambiguous/uncheckable references when the file tree changes.

    Those references have no stored row. Extracting their targets is cheap;
    only documents naming a changed path need resolution and symbol history.
    """
    paths = update.added_paths | update.removed_paths
    if not paths:
        return set()
    affected = set()
    for doc, raw in sources.items():
        if not is_checkable_document(doc) or len(raw) > MAX_DOC_BYTES:
            continue
        refs = extract(raw.decode("utf-8", errors="replace"), doc)
        if any(
            ref.kind in {DriftKind.PATH, DriftKind.LINK, DriftKind.ANCHOR}
            and _target_changed(doc, ref.target, paths)
            for ref in refs
        ):
            affected.add(doc)
    return affected
