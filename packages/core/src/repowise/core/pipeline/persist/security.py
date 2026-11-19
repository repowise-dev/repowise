"""Security finding writers."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_security_findings(result: Any, session: Any, repo_id: str) -> None:
    """Scan every parsed file's source and replace its security findings.

    Source bytes come from ``result.source_map`` (ingestion read them once);
    ``FileInfo`` itself carries no content, only ``content_hash``, so reading
    it off ``file_info`` yields empty text and a scan that can never fire.
    Resume views without a ``source_map`` degrade to the symbol-name scan.
    """
    source_map = getattr(result, "source_map", None) or {}
    await _replace_security_findings(session, repo_id, result.parsed_files, source_map)


async def persist_incremental_security_findings(
    session: Any,
    repo_id: str,
    parsed_files: list[Any] | None,
    source_map: dict[str, Any] | None,
    changed_paths: list[str],
) -> None:
    """Rescan the changed and added files' working-tree security findings.

    Without this, update kept every finding from the last full index: new
    secrets in changed files were missed and old rows pointed at moved lines.
    Deleted files are dropped by ``prune_deleted_file_rows``. No source map
    means no text to scan, and scanning empty text would wipe real findings,
    so that case is a no-op.
    """
    if not parsed_files or not source_map:
        return
    changed = set(changed_paths)
    targets = [pf for pf in parsed_files if pf.file_info.path in changed]
    if targets:
        await _replace_security_findings(session, repo_id, targets, source_map)


async def _replace_security_findings(
    session: Any, repo_id: str, parsed_files: Any, source_map: dict[str, Any]
) -> None:
    from repowise.core.analysis.security_scan import SecurityScanner, scan_source_map

    # The scan is CPU-bound regex work over every file's text; on a large repo
    # it runs for seconds, so keep it off the event loop.
    findings_by_file, scanned_paths = await asyncio.to_thread(
        scan_source_map, parsed_files, source_map
    )
    await SecurityScanner(session, repo_id).replace_findings(findings_by_file, scanned_paths)
