"""Omission refs: content a truncation marker left in the omission store."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.server.mcp_server._meta import build_meta as _build_meta


def _extract_omission_ref(symbol_id: str) -> str | None:
    """Return the 12-hex omission ref when *symbol_id* is ref-shaped, else None."""
    from repowise.server.mcp_server._references import omission_reference

    canonical = omission_reference(symbol_id)
    return canonical.removeprefix("repowise#") if canonical else None


def _resolve_omission_ref(
    symbol_id: str,
    ref: str,
    query: str | None,
    repo_root: Any,
    t0: float,
    other_roots: Sequence[tuple[str, Path]] = (),
) -> dict:
    """Look *ref* up in the omission store(s) and shape a tool response.

    Checks the repo-local store first, then the stores of *other_roots* (the
    other workspace repos, whose responses write their own), then the
    user-level fallback — the same order as ``repowise expand``. Stores are
    only opened when the DB file already exists (opening would otherwise
    create an empty one). A ref is a content hash, so the first store holding
    it has the content.
    """
    from repowise.core.distill.store import OmissionStore, default_store_path

    canonical_ref = f"repowise#{ref}"
    candidates: list[tuple[str | None, Path]] = []
    if repo_root:
        candidates.append((None, default_store_path(Path(str(repo_root)))))
    for alias, root in other_roots:
        candidates.append((alias, default_store_path(root)))
    home_store = default_store_path(Path.home())
    if home_store not in {path for _alias, path in candidates}:
        candidates.append((None, home_store))

    record: dict | None = None
    found_in: str | None = None
    for alias, db_path in candidates:
        try:
            if not db_path.exists():
                continue
            store = OmissionStore(db_path)
            try:
                record = store.get_record(ref, query=query)
            finally:
                store.close()
        except (OSError, sqlite3.Error):
            # An inaccessible fallback store is equivalent to a missing ref.
            # Recovery must never make get_symbol itself fail.
            continue
        if record is not None:
            found_in = alias
            break

    if record is None:
        from repowise.server.mcp_server._savings.interaction import note_error_reason

        note_error_reason("not_found")
        return {
            "symbol_id": symbol_id,
            "ref": canonical_ref,
            "error": (
                f"No stored content for omission ref {ref!r} — it may have "
                "expired (7-day TTL), been pruned, or been produced in a "
                "different repo. Re-run the original call for fresh content."
            ),
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    created = record.get("created_at")
    response: dict[str, Any] = {
        **({"repo": found_in} if found_in else {}),
        "symbol_id": symbol_id,
        "ref": canonical_ref,
        "kind": "omission",
        "source": record.get("source"),
        "original_tokens": record.get("original_tokens"),
        "content": record.get("content"),
        "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
    }
    if isinstance(created, (int, float)):
        response["created_at"] = datetime.fromtimestamp(created, tz=UTC).isoformat()
    if query is not None:
        response["query"] = query
        if not record.get("content"):
            response["note"] = "No lines matched the query; omit query for the full content."
    return response
