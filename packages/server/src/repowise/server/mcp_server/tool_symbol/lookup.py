"""Index lookups: the shared id grammar, name scans and suggestions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._symbol_lookup import (
    NAME_SEPARATORS,
    bare_name,
    name_variants,
    order_candidates,
    parse_symbol_id,
    resolve_symbol_rows,
    symbol_id_variants,
)

# Rows read when matching a name across files. A scan that fills it is not
# exhaustive, so its single survivor is never taken as the only match.
_NAME_SCAN_CAP = 20


# The symbol-id grammar, separator normalisation and lookup ladder are shared
# with get_context (see _symbol_lookup for why they cannot diverge). Re-exported
# under the module-private names this file has always used.
_NAME_SEPARATORS = NAME_SEPARATORS
_parse_symbol_id = parse_symbol_id
_name_variants = name_variants
_symbol_id_variants = symbol_id_variants
_bare_name = bare_name
_order_candidates = order_candidates
_resolve_symbol = resolve_symbol_rows


async def _symbols_named(
    session, repo_id: str, symbol_id: str, exclude_spec
) -> tuple[list[str], bool]:
    """Non-excluded ids of symbols carrying the name in *symbol_id*.

    The second value says whether the scan saw every row with that name, so a
    caller can tell "exactly one" from "one survivor of a capped scan".
    """
    qualified = "::" in symbol_id
    # A bare name is matched whole: it has no path half to strip.
    name = _parse_symbol_id(symbol_id)[1] if qualified else symbol_id.strip()
    if not name:
        return [], True
    bare = _bare_name(name) if qualified else name
    res = await session.execute(
        select(WikiSymbol.symbol_id, WikiSymbol.file_path)
        .where(WikiSymbol.repository_id == repo_id, WikiSymbol.name == bare)
        .order_by(WikiSymbol.symbol_id)
        .limit(_NAME_SCAN_CAP)
    )
    rows = res.all()
    ids = [sid for sid, fpath in rows if sid and not _pkg.is_excluded(fpath, exclude_spec)]
    return list(dict.fromkeys(ids)), len(rows) < _NAME_SCAN_CAP


async def _symbol_suggestions(session, repo_id: str, symbol_id: str, exclude_spec) -> list[str]:
    """Concrete symbol_ids to retry when a lookup misses entirely.

    The caller usually had the right leaf name but a wrong or partial path.
    Match that name across every file and hand back real ids (path-qualified)
    the agent can pass straight back to get_symbol — a bare "not found" would
    otherwise send it to get_context or a whole-file Read.
    """
    ids, _ = await _symbols_named(session, repo_id, symbol_id, exclude_spec)
    return ids[:5]


async def _rows_in_scope(
    ctx: Any, repository: Any, symbol_id: str, rows: list[Any], exclude_spec: Any
) -> list[Any]:
    """*rows* outside the exclusion spec; a bare name one symbol carries resolves to it."""
    rows = [r for r in rows if not _pkg.is_excluded(r.file_path, exclude_spec)]
    if not rows and "::" not in symbol_id:
        # A bare name one symbol carries is that symbol; several stay suggestions.
        async with get_session(ctx.session_factory) as session:
            named, complete = await _symbols_named(session, repository.id, symbol_id, exclude_spec)
            if complete and len(named) == 1:
                rows = await _resolve_symbol(session, repository.id, named[0])
    return rows
