"""get_context ``skeleton`` block: a body-elided rendering of one file."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.mcp_server._budget import DEFAULT_RESPONSE_CHARS

#: Same threshold get_symbol uses to outline a container instead of inlining it.
_FOCUS_CONTAINER_MAX_CHARS = DEFAULT_RESPONSE_CHARS // 2


async def _resolve_skeleton(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str | None,
    result_data: dict[str, Any],
    *,
    repo_root: Any = None,
    mode: str = "smart",
) -> None:
    """Resolve ``include=["skeleton"]`` — a body-elided rendering of one file.

    Slices the on-disk source on the line bounds persisted at index time
    (zero parsing), keeping every signature and the bodies of the
    highest-PageRank symbols under a token budget. ``include=["skeleton+"]``
    passes ``mode="plus"``: all non-function code kept, every function and
    method body elided. A ``file.py::Symbol`` target renders its file with
    that symbol's whole body and every other symbol as its signature.
    """
    # A "file.py::Symbol" target renders the file defining the symbol; other
    # non-file targets have no file to render.
    is_symbol_target = "::" in target
    file_target = target.split("::", 1)[0] if is_symbol_target else target
    if not is_symbol_target and target_type != "file":
        result_data["skeleton"] = {"error": "skeleton requires a file target; pass the file path."}
        return
    if not repo_root:
        result_data["skeleton"] = {"error": "MCP server has no repo path configured."}
        return

    from pathlib import Path

    from repowise.core.distill.skeleton import SkeletonSymbol, build_skeleton
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server._symbol_lookup import symbol_id_variants

    repo_id = repository.id
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path == file_target,
        )
    )
    rows = list(res.scalars().all())

    # Symbol-node PageRank is the importance signal for smart body retention.
    pr_res = await session.execute(
        select(GraphNode.name, GraphNode.pagerank).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == file_target,
        )
    )
    pagerank = {name: pr or 0.0 for name, pr in pr_res.all() if name}

    repo_path = Path(str(repo_root))
    abs_path = (repo_path / file_target).resolve()
    try:
        abs_path.relative_to(repo_path.resolve())
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        result_data["skeleton"] = {
            "error": "Source file could not be read; it may have moved since indexing."
        }
        return

    # The skeleton claims ``verified: True``, so each stored bound is checked
    # against the live source first; a symbol that cannot be re-located is
    # dropped rather than rendering the wrong lines.
    from repowise.server.mcp_server._verify import check_symbol_bounds

    focus_ids = set(symbol_id_variants(target)) if is_symbol_target else set()
    source_lines = source.splitlines()
    symbols = []
    for r in rows:
        check = check_symbol_bounds(r, source)
        if check.approximate:
            continue
        focus = r.symbol_id in focus_ids
        if focus and r.kind not in ("function", "method"):
            # Ceiling: a container body over the get_symbol outline threshold is
            # not inlined; the ranked skeleton shows its methods as signatures.
            span = source_lines[check.start_line - 1 : check.end_line]
            focus = sum(len(ln) + 1 for ln in span) <= _FOCUS_CONTAINER_MAX_CHARS
        symbols.append(
            SkeletonSymbol(
                name=r.name,
                kind=r.kind,
                start_line=check.start_line,
                end_line=check.end_line,
                signature=r.signature,
                importance=pagerank.get(r.name, 0.0),
                focus=focus,
            )
        )
    result = build_skeleton(
        source,
        symbols,
        mode=mode,
        hotspot=bool(result_data.get("hotspot")),
    )
    result_data["skeleton"] = {
        "mode": result.mode,
        "tokens": result.skeleton_tokens,
        "full_tokens": result.full_tokens,
        "pct_of_full": round(result.pct_of_full, 1),
        "bodies_kept": list(result.bodies_kept),
        "text": result.text,
        # The skeleton is rendered from the live source file read above —
        # its text is fresh by construction. Part of the trust contract:
        # a verified response never needs a follow-up Read.
        "verified": True,
    }
    focused = any(sym.focus for sym in symbols)
    if is_symbol_target:
        name = target.split("::", 1)[1]
        result_data["skeleton"]["of_file"] = file_target
        if focused:
            result_data["skeleton"]["symbol_hint"] = (
                f"Skeleton of the file defining '{name}': its full body, every "
                "other symbol as its signature."
            )
        else:
            # No row for the symbol passed the bounds check, or it is a container
            # over the ceiling, so its body is not guaranteed here.
            result_data["skeleton"]["symbol_hint"] = (
                f"Skeleton of the file defining '{name}'. For that "
                f"symbol's full body call get_symbol('{target}')."
            )
    if result.mode == "raw":
        result_data["skeleton"]["note"] = (
            "No usable symbol bounds for this file — returned source as-is."
        )
    elif result.pct_of_full > 40.0 and not focused:
        # A skeleton this close to the source makes a Read nearly as cheap;
        # a focused skeleton is not the case (it is the symbol plus a file map).
        result_data["skeleton"]["mostly_full"] = True
        result_data["skeleton"]["note"] = (
            f"Skeleton is {round(result.pct_of_full, 1)}% of the full file — "
            "a direct Read costs little more."
        )
