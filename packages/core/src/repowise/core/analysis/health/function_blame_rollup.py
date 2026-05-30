"""Per-function blame rollup → ``git_function_blame`` rows.

Turns the in-memory per-line :class:`BlameIndex` (built once per file during
FULL-tier health analysis) plus the walker's per-function line ranges into one
persisted rollup row per *modified* function. The biomarkers
(``function_hotspot``/``code_age_volatility``) already project the same blame
index onto each function's range and then discard it; this captures that work so
a function-level health surface can read it without re-blaming.

The transform is pure (no git, no I/O) — it only reads the already-materialised
blame index and the walker output — so it is cheap and unit-testable. Only
functions with ``mod_count > 0`` are emitted, bounding the table to genuinely
churned functions (the "no signal" convention used by the biomarkers).
"""

from __future__ import annotations

from typing import Any

from ...ingestion.git_indexer.function_blame import (
    BlameIndex,
    distinct_commits_in_range,
    median_author_time_in_range,
    owner_in_range,
    recent_commits_in_range,
)

# Recent-modification window for the rollup. Broader than the 30-day window
# ``code_age_volatility`` uses for its finding gate — the persisted row is a
# general-purpose signal, so a 90-day window matches the rest of the git data.
_RECENT_WINDOW_DAYS = 90


def build_function_blame_rows(
    walked: list[tuple[Any, Any]],
    git_meta_map: dict[str, dict],
    *,
    now_ts: int,
    recent_window_days: int = _RECENT_WINDOW_DAYS,
) -> list[dict]:
    """Build ``git_function_blame`` row dicts from walked files + blame indexes.

    *walked* is the engine's ``[(parsed_file, FileComplexity)]`` list; each
    file's blame index is looked up in *git_meta_map* under ``"blame_index"``
    (the FULL-tier git tier attaches it there). *now_ts* anchors the recent
    window (unix seconds) — pass the index-time clock.
    """
    since = now_ts - recent_window_days * 86400
    rows: list[dict] = []

    for pf, fcx in walked:
        path = pf.file_info.path
        meta = git_meta_map.get(path) or {}
        idx = meta.get("blame_index")
        if not isinstance(idx, BlameIndex) or not idx.lines:
            continue
        for fc in fcx.functions:
            start, end = fc.start_line, fc.end_line
            mod_count = len(distinct_commits_in_range(idx, start, end))
            if mod_count == 0:
                # No blame coverage / unmodified — the "no signal" outcome.
                continue
            recent_mod = len(recent_commits_in_range(idx, start, end, since_unix_ts=since))
            median_ts = median_author_time_in_range(idx, start, end)
            owner_name, owner_email, owner_pct = owner_in_range(idx, start, end)
            rows.append(
                {
                    "symbol_id": f"{path}::{fc.name}",
                    "file_path": path,
                    "function_name": fc.name,
                    "start_line": start,
                    "end_line": end,
                    "line_count": max(0, end - start + 1),
                    "mod_count": mod_count,
                    "recent_mod_count": recent_mod,
                    "median_author_time": median_ts,
                    "owner_name": owner_name,
                    "owner_email": owner_email,
                    "owner_line_pct": owner_pct,
                }
            )
    return rows
