"""Single-target resolution for get_context.

``_resolve_one_target`` walks the file → module → symbol → fallback ladder for
one target and assembles its triage card (docs, triage signals, ownership,
last_change, decisions, freshness, KG layer/tour, and the opt-in enrichment
blocks delegated to ``enrichment``).
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.generation.page_selection import STALE_STATUSES
from repowise.core.persistence.crud import get_kg_layers, get_kg_tour_steps
from repowise.core.persistence.decision_graph import get_governing_decisions
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _decision_body,
    escape_like,
    filter_dicts_by_key,
    filter_path_list,
    is_excluded,
    read_repo_file_text,
)
from repowise.server.mcp_server._symbol_lookup import resolve_symbol_rows
from repowise.server.mcp_server.tool_context.enrichment import (
    _resolve_call_graph,
    _resolve_community,
    _resolve_health,
    _resolve_metrics,
    _resolve_skeleton,
)
from repowise.server.mcp_server.tool_context.kg import (
    _classify_file_role,
    _find_layer_for_file,
    _find_tour_step_for_file,
)
from repowise.server.mcp_server.tool_risk.assessment import fix_annotation

# Skeleton-by-default is GONE; ``include=["skeleton"]`` still serves it in full.
#
# It was introduced on the claim that "a 1,400-line file's default card costs
# ~2.5k tokens for 16 bare signatures, while the smart skeleton costs ~1.7k" —
# strictly better per token. Re-measured 2026-08-11 on pinned Textualize/rich,
# whole serialised cards in characters, auto-upgrade suppressed for the
# comparison:
#
#   target          auto card   symbol-list card   skeleton text alone
#   rich/ansi.py        6,585              2,171                 5,295
#   rich/text.py       13,365              8,562                12,247
#   rich/color.py       8,591              4,818                 7,405
#
# The symbol list is 1,321 chars on ansi.py where the skeleton text is 5,295.
# The original claim is inverted on this corpus, and the auto card was 73-91%
# source text on every sample — text the agent can Read for ~1/5 the marginal
# cost of a mid-session tool result, which is what the block's own
# ``mostly_full`` note ("a direct Read costs little more") was already saying
# at the agent's expense. So the default card is the symbol list again and the
# skeleton is opt-in. If you restore an auto-upgrade, re-run the measurement
# first: the numbers above are the bar.


def _synthesize_structural_summary(file_path: str, classes: list[str], functions: list[str]) -> str:
    """Build a deterministic 1-line summary when no LLM-generated summary exists.

    Used in --index-only mode (no wiki pages) and as a fallback when an LLM
    page predates the summary column. Always returns a non-empty string so the
    agent never sees a missing field.
    """
    name = file_path.rsplit("/", 1)[-1]
    parts: list[str] = []
    if classes:
        head = ", ".join(classes[:3])
        more = f" (+{len(classes) - 3} more)" if len(classes) > 3 else ""
        parts.append(f"defines {head}{more}")
    if functions:
        head = ", ".join(functions[:3])
        more = f" (+{len(functions) - 3} more)" if len(functions) > 3 else ""
        parts.append(f"function{'s' if len(functions) > 1 else ''} {head}{more}")
    if not parts:
        return f"{name}: {_NO_SYMBOL_SUMMARY_SUFFIX}"
    return f"{name}: " + "; ".join(parts) + "."


# The summary a file with no indexed symbols falls back to. Named so the
# preview block below can recognise its own stub and correct it rather than
# string-matching the sentence in two places.
_NO_SYMBOL_SUMMARY_SUFFIX = "empty or non-symbol file"


def _preview_summary(file_path: str, preview: dict[str, Any]) -> str:
    """One truthful line about a symbol-less file, built from its own counts."""
    name = file_path.rsplit("/", 1)[-1]
    lines = preview.get("lines", 0)
    if not lines:
        return f"{name}: empty file"
    headings = preview.get("headings")
    if headings:
        return f"{name}: {lines}-line document, {len(headings)} headings, no indexed symbols."
    return f"{name}: {lines} lines, no indexed symbols."


# A file with no indexed symbols (README, YAML config, SQL, plain text) used to
# get a card whose entire content was "<name>: empty or non-symbol file", a
# reply that restates the filename and answers nothing, so the next move was
# always a Read or a grep the tool could have saved. These bounds keep the
# replacement preview cheap enough to stay on by default.
_PREVIEW_MAX_LINES = 15
_PREVIEW_MAX_LINE_CHARS = 120
# Beyond this the file is big enough that a preview would misrepresent it; the
# counts and the "go Read it" note are the honest reply.
_PREVIEW_MAX_BYTES = 2_000_000

_MARKDOWN_EXTS = (".md", ".markdown", ".mdx", ".rst")


def _outline_lines(text: str, file_path: str) -> tuple[str, list[str]]:
    """Pick the most informative ~15 lines of a symbol-less file.

    Markdown-ish files get their heading spine, which is a genuine table of
    contents. Everything else gets its first non-blank, non-comment lines,
    which for config and data files is where the keys live. Returns the kind of
    excerpt chosen so the caller can label it truthfully.
    """
    lines = text.splitlines()
    if file_path.lower().endswith(_MARKDOWN_EXTS):
        headings = [ln.strip() for ln in lines if ln.lstrip().startswith("#")]
        # An .rst or heading-less .md falls through to the head-lines form
        # rather than reporting an empty outline.
        if headings:
            return "headings", headings[:_PREVIEW_MAX_LINES]
    head = [ln.rstrip() for ln in lines if ln.strip()]
    return "head", head[:_PREVIEW_MAX_LINES]


def _file_preview(repo_root: Any, file_path: str) -> dict[str, Any] | None:
    """Cheap, true facts about a file the symbol index has nothing to say about.

    Only counts and verbatim excerpts, nothing inferred. Returns None when the
    file cannot be read, so the caller keeps its existing behaviour rather than
    reporting an empty preview as if the file were empty.
    """
    text = read_repo_file_text(repo_root, file_path)
    if text is None:
        return None

    lines = text.splitlines()
    preview: dict[str, Any] = {"lines": len(lines), "chars": len(text)}
    if not text.strip():
        preview["note"] = "File is empty."
        return preview
    if len(text) > _PREVIEW_MAX_BYTES:
        preview["note"] = "File is too large to preview; Read it directly."
        return preview

    kind, excerpt = _outline_lines(text, file_path)
    if excerpt:
        preview[kind] = [ln[:_PREVIEW_MAX_LINE_CHARS] for ln in excerpt]
    preview["note"] = (
        "This file has no indexed symbols, so there is no structural card for "
        "it. The fields above are counts and verbatim excerpts. Read the file "
        "for its full content."
    )
    return preview


def _clean_signature(signature: str | None) -> str:
    """Collapse a stored signature onto one line.

    Signatures indexed from CRLF files carry literal ``\\r\\n`` plus the
    original indentation — pure token waste in a triage card. Whitespace
    runs collapse to single spaces; the text is unchanged otherwise.
    """
    return " ".join((signature or "").split())


def _size_exclusion_note(repo_root: Any, target: str) -> str | None:
    """Explain a missing file that ingestion dropped on size, else None.

    Computed live from the file on disk rather than read from the index,
    because a file excluded from ingestion leaves no row to read. Delegates the
    actual rule to ``size_verdict`` so this answer cannot drift from the code
    that produced the exclusion.
    """
    if repo_root is None:
        return None
    try:
        from repowise.core.ingestion.traverser import size_verdict

        root = Path(str(repo_root))
        abs_path = (root / target).resolve()
        # An index row is not a trust boundary; refuse anything outside the repo.
        abs_path.relative_to(root.resolve())
        size_bytes = abs_path.stat().st_size
        verdict = size_verdict(abs_path, size_bytes)
    except (OSError, ValueError):
        return None
    if verdict is None or not verdict.is_source:
        return None
    detail = {
        "minified": "it looks minified (machine-packed, not hand-written)",
        "unreadable": "it could not be read",
    }.get(verdict.reason, "it exceeds the maximum size for an indexed source file")
    return (
        f"'{target}' is not indexed: {detail} "
        f"({size_bytes // 1024:,} KB). It has no page and no symbols, and "
        "`repowise update` will not create them. Read the file directly."
    )


async def _resolve_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    include: set[str] | None,
    compact: bool = False,
    *,
    exclude_spec: Any = None,
    repo_root: Any = None,
) -> dict:
    """Resolve a single target and return its full context."""
    repo_id = repository.id
    result_data: dict[str, Any] = {}

    # Reject excluded file / ``path::Name`` targets outright (bare symbol names
    # aren't path-matchable here and fall through to neighbor filtering).
    gate_path = target.split("::", 1)[0] if "::" in target else target
    if is_excluded(gate_path, exclude_spec):
        return {
            "target": target,
            "error": f"'{target}' is excluded by exclude_patterns configuration",
        }

    # --- Determine target type ---
    # 1. Try file page (most common)
    page_id = f"file_page:{target}"
    page = await session.get(Page, page_id)
    target_type = None
    file_path_for_git: str | None = None
    # Set only when a symbol target resolved through the call graph rather than
    # the symbol index (index-only mode); carries the fields the node has.
    graph_symbol: GraphNode | None = None

    if page and page.repository_id == repo_id:
        target_type = "file"
        file_path_for_git = target
    else:
        # 1b. Normalise directory targets: strip trailing slash and try module
        clean_target = target.rstrip("/")
        # 2. Try module page (exact, then cleaned, then partial)
        res = await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type == "module_page",
                Page.target_path == target,
            )
        )
        page = res.scalar_one_or_none()
        if page is None and clean_target != target:
            res = await session.execute(
                select(Page).where(
                    Page.repository_id == repo_id,
                    Page.page_type == "module_page",
                    Page.target_path == clean_target,
                )
            )
            page = res.scalar_one_or_none()
        if page is None:
            # Partial match fallback for modules — but only on a path-segment
            # boundary, so "api" matches "src/api" yet "apiclient"/"pi" do not.
            # Curated module ids are path-shaped, so a raw substring match can
            # (a) hit 2+ module paths (→ MultipleResultsFound) and (b) shadow a
            # real file of the same name. Guard against both here.
            #
            # First: if the target is itself a known real file (present in
            # git_metadata, the same source the git fallback rung uses), do NOT
            # let a partial module match preempt that — fall through so the
            # ladder reaches the "exists but no wiki page" rung below.
            file_meta_res = await session.execute(
                select(GitMetadata.file_path).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == clean_target,
                )
            )
            is_known_file = file_meta_res.scalar_one_or_none() is not None
            if not is_known_file:
                esc = escape_like(clean_target)
                res = await session.execute(
                    select(Page).where(
                        Page.repository_id == repo_id,
                        Page.page_type == "module_page",
                        or_(
                            Page.target_path == clean_target,
                            Page.target_path.like(f"%/{esc}", escape=LIKE_ESCAPE),
                            Page.target_path.like(f"{esc}/%", escape=LIKE_ESCAPE),
                            Page.target_path.like(f"%/{esc}/%", escape=LIKE_ESCAPE),
                        ),
                    )
                )
                # Deterministic pick when several module paths match: shortest
                # target_path first, ties broken lexicographically. Module
                # counts are small, so picking in Python is robust and cheap.
                candidates = sorted(
                    res.scalars().all(), key=lambda p: (len(p.target_path), p.target_path)
                )
                if candidates:
                    page = candidates[0]
        if page:
            target_type = "module"
        else:
            # 3. Try symbol.
            #
            # A "{path}::{Name}" target goes through the shared symbol lookup,
            # the same ladder get_symbol uses, so an id lifted from one tool's
            # response resolves in the other, in whichever separator style the
            # caller wrote the qualified part. Matching such a target verbatim
            # against WikiSymbol.name can only ever miss (a stored name is one
            # segment, never a path-qualified id), and the ilike below would
            # then scan for a "%path::Class.method%" substring that no name
            # column contains. Both rungs are skipped for qualified ids.
            if "::" in target:
                sym_matches = await resolve_symbol_rows(session, repo_id, target)
            else:
                res = await session.execute(
                    select(WikiSymbol).where(
                        WikiSymbol.repository_id == repo_id,
                        WikiSymbol.name == target,
                    )
                )
                sym_matches = list(res.scalars().all())
                if not sym_matches:
                    res = await session.execute(
                        select(WikiSymbol)
                        .where(
                            WikiSymbol.repository_id == repo_id,
                            WikiSymbol.name.ilike(f"%{escape_like(target)}%", escape=LIKE_ESCAPE),
                        )
                        .limit(10)
                    )
                    sym_matches = list(res.scalars().all())
            if sym_matches:
                target_type = "symbol"
                file_path_for_git = sym_matches[0].file_path
            else:
                # 4. Try file page by target_path search
                res = await session.execute(
                    select(Page).where(
                        Page.repository_id == repo_id,
                        Page.page_type == "file_page",
                        Page.target_path == target,
                    )
                )
                page = res.scalar_one_or_none()
                if page:
                    target_type = "file"
                    file_path_for_git = target

    if target_type is None:
        # Fallback 1: index-only mode (no wiki pages). Return the graph node.
        #
        # The call graph keys its symbol nodes as "{path}::{Class}::{method}",
        # so a symbol target with no WikiSymbol row matches here, and used to
        # be typed as a *file*. The card then looked up symbols whose file_path
        # was the whole id, found none, and reported the symbol as an "empty or
        # non-symbol file". Type the node by what it is instead: a symbol node
        # is a symbol target, and its file is the node's file, not its id.
        res = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id == target,
            )
        )
        gnode = res.scalar_one_or_none()
        if gnode is not None and gnode.node_type == "symbol":
            target_type = "symbol"
            graph_symbol = gnode
            file_path_for_git = gnode.file_path
            page = None
        elif gnode is not None:
            target_type = "file"
            file_path_for_git = target
            page = None  # no wiki page; subsequent blocks must guard for this

        # Fallback 2: check git_metadata — file may exist but have no wiki page AND no graph node
        if target_type is None:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == target,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                # Say so when the file was excluded on size. The generic answer
                # below ("too few symbols or below the PageRank threshold")
                # sends the reader to regenerate docs, which cannot help: the
                # file was never read, so no amount of updating will produce a
                # page. Computed from the same function that made the decision,
                # so the two cannot drift (#1237).
                size_note = _size_exclusion_note(repo_root, target)
                return {
                    "target": target,
                    "error": size_note
                    or (
                        f"'{target}' exists in the repository but has no wiki page. "
                        "This usually means the file has too few symbols or is below "
                        "the PageRank threshold. Run `repowise update` to regenerate docs."
                    ),
                    "exists_in_git": True,
                    "last_commit_at": meta.last_commit_at.isoformat()
                    if meta.last_commit_at
                    else None,
                    "primary_owner": meta.primary_owner_name,
                    "is_hotspot": meta.is_hotspot,
                }

        # Fallback 2b: legacy module ids. Wiki modules used to be keyed by
        # community ordinal ("community-12"); they are now keyed by directory
        # path. Point old agent habits at the new vocabulary.
        if target_type is None and re.fullmatch(r"community[-_]\d+", clean_target, re.IGNORECASE):
            res = await session.execute(
                select(Page.target_path)
                .where(
                    Page.repository_id == repo_id,
                    Page.page_type == "module_page",
                )
                .order_by(Page.target_path)
                .limit(10)
            )
            module_paths = filter_path_list([row[0] for row in res.all()], exclude_spec)
            return {
                "target": target,
                "error": (
                    f"Target not found: '{target}'. Module pages are no longer "
                    "keyed by community ordinal — pass the module's directory "
                    "path instead (see suggestions)."
                ),
                "suggestions": module_paths,
            }

        # Fallback 2c: a "{path}::{Name}" target whose symbol half did not
        # resolve, but whose file half is a real indexed file. The caller asked
        # about something *in* that file, so the file's card is a partial answer
        # and a strictly better reply than "not found": it carries the symbol
        # list, which is where the correct id was going to come from anyway.
        # Reported as ``resolved_to`` so the degrade is legible rather than
        # looking like the symbol was found.
        if target_type is None and "::" in target:
            file_part = target.split("::", 1)[0]
            if file_part and file_part != target and not is_excluded(file_part, exclude_spec):
                # file_part contains no "::", so this recursion is depth-1.
                card = await _resolve_one_target(
                    session,
                    repository,
                    file_part,
                    include,
                    compact,
                    exclude_spec=exclude_spec,
                    repo_root=repo_root,
                )
                if "error" not in card:
                    card["target"] = target
                    card["resolved_to"] = file_part
                    card["note"] = (
                        f"No symbol matched {target.split('::', 1)[1]!r} in this file; "
                        f"showing the card for {file_part!r} instead. Pick the symbol "
                        "you meant from the symbol list."
                    )
                    return card

        # Fallback 3: fuzzy path suggestions — match by filename or partial path.
        # Only runs if the prior fallbacks didn't resolve the target.
        if target_type is None:
            # For directory-like targets, suggest files within that directory
            dir_prefix = clean_target.rstrip("/") + "/"
            res = await session.execute(
                select(GitMetadata.file_path)
                .where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path.like(f"{escape_like(dir_prefix)}%", escape=LIKE_ESCAPE),
                )
                .limit(5)
            )
            suggestions = [row[0] for row in res.all()]
            if not suggestions:
                # Fall back to filename / partial path match
                tail = target.rsplit("/", 1)[-1]
                res = await session.execute(
                    select(GitMetadata.file_path)
                    .where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.file_path.contains(tail),
                    )
                    .limit(5)
                )
                suggestions = [row[0] for row in res.all() if row[0] != target]
            suggestions = filter_path_list(suggestions, exclude_spec)
            if suggestions:
                return {
                    "target": target,
                    "error": f"Target not found: '{target}'",
                    "suggestions": suggestions,
                }
            return {"target": target, "error": f"Target not found: '{target}'"}

    result_data["target"] = target
    result_data["type"] = target_type

    # Tombstone redirect: the page documents a file deleted or renamed since
    # indexing. A "fresh" card here is an active trap — return the redirect
    # instead of the card.
    if page is not None and getattr(page, "freshness_status", "") == "tombstone":
        import json as _json_ts

        try:
            successors = _json_ts.loads(page.metadata_json or "{}").get("successor_paths") or []
        except (ValueError, TypeError):
            successors = []
        result_data["error"] = (
            f"'{target}' was deleted or renamed after indexing — this page is a tombstone."
        )
        if successors:
            result_data["successor_paths"] = successors
            result_data["hint"] = f"Content moved; call get_context on {successors[0]!r} instead."
        return result_data

    # --- Parent page (position in the concept tree) -----------------------
    # Every placed page now carries parent_page_id, so a file target can point
    # up to the subsystem/concept page that documents it, and a concept page
    # up to its layer. One extra get() on the already-open session; the row is
    # a full ORM object here, so parent_page_id is already loaded.
    if page is not None and page.parent_page_id:
        parent = await session.get(Page, page.parent_page_id)
        if (
            parent is not None
            and parent.repository_id == repo_id
            and getattr(parent, "freshness_status", "") != "tombstone"
        ):
            # Skip a tombstoned parent: pointing an agent up-tree at a page
            # whose directory was deleted or renamed since indexing is the
            # same trap the target's own tombstone redirect above avoids.
            result_data["parent_page"] = {
                "title": parent.title,
                "target_path": parent.target_path,
                "section": parent.section_number,
            }

    want_skeleton = bool(include and "skeleton" in include)

    # --- Docs ---
    # "full_doc" implies "docs" — entering the docs block whenever either is requested.
    if include is None or "docs" in include or "full_doc" in include:
        want_full_doc = bool(include and "full_doc" in include)
        docs: dict[str, Any] = {}
        if target_type == "file":
            if page is not None:
                docs["title"] = page.title
                docs["summary"] = page.summary or ""
                if want_full_doc:
                    docs["content_md"] = page.content
                if page.human_notes:
                    docs["human_notes"] = page.human_notes
            # Symbols in this file
            res = await session.execute(
                select(WikiSymbol).where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.file_path == target,
                )
            )
            symbols = res.scalars().all()
            classes = [s.name for s in symbols if s.kind == "class"]
            functions = [s.name for s in symbols if s.kind in ("function", "method")]
            if want_skeleton:
                # A requested skeleton suppresses the symbol list: it already
                # renders every signature with line bounds, so repeating the
                # list in docs would roughly double the response for zero
                # information. Keep the cheap title/summary card only.
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
            elif compact:
                # Compact mode: name+kind+signature+line+symbol_id only. Drops
                # docstrings, line ranges, structure, and imported_by — those
                # live behind compact=False or include= flags. The symbol_id
                # is the canonical handle the caller pipes straight into
                # get_symbol when it wants bytes.
                #
                # Cap at 40 symbols so a dense generated file (protobuf
                # wrappers, vendored libs) can't blow the triage card past
                # the agent's budget. Order matches WikiSymbol.start_line
                # (assigned at index time), so the head is the navigationally
                # useful slice.
                symbol_cap = 40
                visible = list(symbols)[:symbol_cap]
                docs["symbols"] = [
                    {
                        "name": s.name,
                        "kind": s.kind,
                        "signature": _clean_signature(s.signature),
                        "line": s.start_line,
                        "symbol_id": s.symbol_id,
                    }
                    for s in visible
                ]
                if len(symbols) > symbol_cap:
                    docs["symbols_truncated"] = {
                        "shown": symbol_cap,
                        "total": len(symbols),
                        "hint": "Call with compact=False or include=['full_doc'] for the full list.",
                    }
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
            else:
                docs["symbols"] = [
                    {
                        "name": s.name,
                        "kind": s.kind,
                        "signature": _clean_signature(s.signature),
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "docstring": (s.docstring or "")[:400],
                    }
                    for s in symbols
                ]
                # Structure summary block — quick scan of what's in the file
                total_loc = max((s.end_line for s in symbols), default=0)
                avg_complexity = (
                    sum(s.complexity_estimate for s in symbols) / len(symbols) if symbols else 0
                )
                docs["structure"] = {
                    "classes": classes,
                    "functions": functions,
                    "symbol_count": len(symbols),
                    "total_loc": total_loc,
                    "avg_complexity": round(avg_complexity, 2),
                }
                # Fallback summary: if no Page (index-only mode) or page.summary
                # is empty, synthesize a deterministic one-liner from structure.
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
                # Importers
                res = await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.repository_id == repo_id,
                        GraphEdge.target_node_id == target,
                    )
                )
                importers = res.scalars().all()
                docs["imported_by"] = filter_path_list(
                    [e.source_node_id for e in importers], exclude_spec
                )

                # Community info (compact=False only, ~80 bytes)
                res = await session.execute(
                    select(GraphNode).where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_id == target,
                    )
                )
                gn = res.scalar_one_or_none()
                if gn and gn.community_id is not None:
                    _cmeta: dict[str, Any] = {}
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        _cmeta = json.loads(gn.community_meta_json or "{}")
                    docs["community"] = {
                        "id": gn.community_id,
                        "label": _cmeta.get("label", ""),
                    }

            # A file the symbol index has nothing for still exists and still has
            # content. Serving counts and a verbatim excerpt costs one read and
            # answers the "what is in here" the caller was asking; the card
            # without it restates the filename. Applies to all three shapes
            # above; none of them has anything to say about a symbol-less file.
            if not symbols:
                preview = _file_preview(repo_root, target)
                if preview is not None:
                    docs["file_preview"] = preview
                    # "empty or non-symbol file" is the only summary a
                    # symbol-less file could get, and next to a preview showing
                    # 800 lines of headings it is simply false. Restate it from
                    # what the preview actually counted.
                    if docs.get("summary", "").endswith(_NO_SYMBOL_SUMMARY_SUFFIX):
                        docs["summary"] = _preview_summary(target, preview)

        elif target_type == "module":
            docs["title"] = page.title
            docs["summary"] = page.summary or ""
            if page.section_number:
                docs["section"] = page.section_number
            if want_full_doc:
                docs["content_md"] = page.content
            # Direct sub-concept children in the tree (nested subsystems, api
            # contracts, symbol spotlights). File children are covered by the
            # member "files" list below, so listing them here too would just
            # double the response; this surfaces the tree's real sub-structure
            # where it exists and is omitted when there is none.
            res = await session.execute(
                select(Page)
                .where(
                    Page.repository_id == repo_id,
                    Page.parent_page_id == page.id,
                    Page.page_type != "file_page",
                )
                .order_by(Page.display_order, Page.target_path)
            )
            child_pages = res.scalars().all()
            if child_pages:
                docs["children"] = filter_dicts_by_key(
                    [
                        {
                            "title": ch.title,
                            "target_path": ch.target_path,
                            "page_type": ch.page_type,
                            "section": ch.section_number,
                        }
                        for ch in child_pages
                    ],
                    "target_path",
                    exclude_spec,
                )
            # Child file pages
            res = await session.execute(
                select(Page).where(
                    Page.repository_id == repo_id,
                    Page.page_type == "file_page",
                    Page.target_path.like(f"{escape_like(page.target_path)}/%", escape=LIKE_ESCAPE),
                )
            )
            file_pages = res.scalars().all()
            docs["files"] = filter_dicts_by_key(
                [
                    {
                        "path": f.target_path,
                        # Page titles are "File: <path>" — pure redundancy
                        # next to the path field. Use the indexed one-line
                        # summary when there is one.
                        "description": (f.summary or "").strip()[:160],
                        "confidence_score": f.confidence,
                    }
                    for f in file_pages
                ],
                "path",
                exclude_spec,
            )

        elif target_type == "symbol":
            # In index-only mode the symbol may exist as a graph node with no
            # WikiSymbol row, so the node carries the fields it has and the
            # rest are simply absent rather than faked.
            sym = sym_matches[0] if sym_matches else graph_symbol  # type: ignore[possibly-undefined]
            docs["name"] = sym.name
            docs["kind"] = sym.kind
            docs["file_path"] = sym.file_path
            for attr, key in (
                ("qualified_name", "qualified_name"),
                ("signature", "signature"),
                ("docstring", "docstring"),
            ):
                value = getattr(sym, attr, None)
                if value:
                    docs[key] = _clean_signature(value) if attr == "signature" else value
            # File page summary (full content gated behind include=["full_doc"])
            sym_page_id = f"file_page:{sym.file_path}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page is not None:
                docs["file_summary"] = sym_page.summary or ""
                if want_full_doc:
                    docs["documentation"] = sym_page.content
            # Used by
            res = await session.execute(
                select(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.target_node_id == sym.file_path,
                )
            )
            edges = res.scalars().all()
            docs["used_by"] = filter_path_list([e.source_node_id for e in edges], exclude_spec)[:20]
            # Candidates
            if len(sym_matches) > 1:  # type: ignore[possibly-undefined]
                docs["candidates"] = filter_dicts_by_key(
                    [
                        {"name": m.name, "kind": m.kind, "file_path": m.file_path}
                        for m in sym_matches[1:5]  # type: ignore[possibly-undefined]
                    ],
                    "file_path",
                    exclude_spec,
                )

        result_data["docs"] = docs

    # --- Triage signals (always on) ---------------------------------------
    # Two single-bit-ish pointers the agent uses to decide its next move:
    #   * ``hotspot``: lights the way to ``get_risk`` for files in the 95th+
    #     churn percentile. Just the boolean — the full risk dossier stays
    #     in ``get_risk`` so the triage card doesn't grow.
    #   * ``fix_history``: the same pointer for the defect signal, and the
    #     reason the card grew by one key. ``hotspot`` alone answers "is this
    #     file busy", which is not the same question as "does this file break",
    #     and get_risk already classifies targets bug-prone off these columns.
    #     Count plus age plus the magnet flag, no symbols and no dossier;
    #     omitted entirely on files with no counted fixes.
    #   * ``decision_records``: titles only, no body. Lights the way to
    #     ``get_why``. We deliberately don't inline the rationale here;
    #     duplicating it across every ``get_context`` response bloats the
    #     cached prompt prefix and defeats the split between the two tools.
    #
    # Cheap: two short queries piggybacking on the session we already opened.
    triage_path = file_path_for_git
    if target_type == "module" and page:
        triage_path = page.target_path
    if triage_path:
        # Same single row, four columns instead of one: no extra round trip.
        triage_meta_res = await session.execute(
            select(
                GitMetadata.is_hotspot,
                GitMetadata.prior_defect_count,
                GitMetadata.bug_magnet,
                GitMetadata.last_fix_at,
            ).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path == triage_path,
            )
        )
        triage_meta = triage_meta_res.one_or_none()
        result_data["hotspot"] = bool(triage_meta.is_hotspot) if triage_meta is not None else False
        if triage_meta is not None:
            # Row exposes the selected columns as attributes, which is exactly
            # the shape fix_annotation reads off a full ORM row.
            fixes = fix_annotation(triage_meta)
            if fixes is not None:
                result_data["fix_history"] = fixes

        # Governing decisions — opt-in only (``include=["decisions"]``).
        # The default triage card omits them: the rich form
        # (id/staleness/verification) is low-signal for an agent's next move and
        # the per-call graph query isn't worth the latency or the cached-prefix
        # weight. Agents that want rationale call get_why directly; opting in
        # here returns a lightweight titles list (no enriched objects).
        if include and "decisions" in include:
            governing: list[DecisionRecord] = []
            seen_ids: set[str] = set()
            for lookup_node in dict.fromkeys(
                [triage_path, target] if triage_path != target else [triage_path]
            ):
                if not lookup_node:
                    continue
                for dr in await get_governing_decisions(session, repo_id, lookup_node):
                    if dr.id not in seen_ids:
                        seen_ids.add(dr.id)
                        governing.append(dr)
            if governing:
                governing_sorted = sorted(governing, key=lambda d: -(d.confidence or 0.0))
                result_data["decision_records"] = [dr.title for dr in governing_sorted[:3]]
                result_data["decision_records_hint"] = (
                    "Decisions touch this file. Call get_why(targets=[...]) for rationale."
                )

    # --- Ownership ---
    if include is None or "ownership" in include:
        ownership: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                ownership["primary_owner"] = meta.primary_owner_name
                ownership["owner_pct"] = meta.primary_owner_commit_pct
                ownership["contributor_count"] = getattr(meta, "contributor_count", 0) or len(
                    json.loads(meta.top_authors_json)
                )
                ownership["bus_factor"] = getattr(meta, "bus_factor", 0) or 0
                # Recent owner (who maintains this file now)
                recent = getattr(meta, "recent_owner_name", None)
                if recent and recent != meta.primary_owner_name:
                    ownership["recent_owner"] = recent
                    ownership["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
                # Agent provenance — only surfaced when agent-attributed
                # commits exist, so human-only files stay noise-free.
                if getattr(meta, "agent_commit_count", 0):
                    ownership["agent_authored_pct"] = getattr(meta, "agent_authored_pct", None)
                    ownership["agent_commit_count"] = meta.agent_commit_count
                    with contextlib.suppress(TypeError, ValueError):
                        ownership["agent_tier_counts"] = json.loads(
                            getattr(meta, "agent_tier_counts_json", None) or "{}"
                        )
            else:
                ownership["primary_owner"] = None
                ownership["owner_pct"] = None
                ownership["contributor_count"] = 0
                ownership["bus_factor"] = 0
        else:
            ownership["primary_owner"] = None
            ownership["owner_pct"] = None
            ownership["contributor_count"] = 0
            ownership["bus_factor"] = 0
        result_data["ownership"] = ownership

    # --- Last change ---
    if include is None or "last_change" in include:
        last_change: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                last_change["date"] = (
                    meta.last_commit_at.isoformat() if meta.last_commit_at else None
                )
                last_change["author"] = meta.primary_owner_name
                last_change["days_ago"] = meta.age_days
            else:
                last_change["date"] = None
                last_change["author"] = None
                last_change["days_ago"] = None
        else:
            last_change["date"] = None
            last_change["author"] = None
            last_change["days_ago"] = None
        result_data["last_change"] = last_change

    # --- Decisions ---
    if include is None or "decisions" in include:
        res = await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repo_id,
            )
        )
        all_decisions = res.scalars().all()
        governing = []
        for d in all_decisions:
            affected_files = json.loads(d.affected_files_json)
            affected_modules = json.loads(d.affected_modules_json)
            if (
                target in affected_files
                or target in affected_modules
                or (file_path_for_git and file_path_for_git in affected_files)
            ):
                governing.append(
                    {
                        "id": d.id,
                        "title": d.title,
                        "status": d.status,
                        "decision": _decision_body(d),
                        "rationale": d.rationale,
                        "confidence": d.confidence,
                    }
                )
        result_data["decisions"] = governing

    # --- Freshness ---
    #
    # ``is_stale`` reads ``freshness_status`` through the same predicate
    # ``repowise generate --stale`` uses, rather than thresholding
    # ``confidence``. Two separate corrections in one line.
    #
    # The axis was wrong: freshness is whether a page has fallen behind the
    # code it documents, confidence is how far its statements can be trusted
    # while it has not. Thresholding the latter reported a *module* page as
    # stale on the day it was built, because generation stamped every module
    # page a keyless run rendered below the threshold. File and symbol targets
    # were unaffected, since their pages are template renders at full
    # confidence, so this was a module-target bug rather than a whole-tool one.
    #
    # The word was also being redefined: importing the CLI's set keeps one
    # meaning of "stale" in the product. A hand-rolled ``!= "fresh"`` would
    # quietly widen it to cover ``tombstone`` and ``outdated`` too, which is a
    # different claim than the one the flag makes.
    if include is None or "freshness" in include:
        freshness: dict[str, Any] = {}

        def _is_stale(row: Page) -> bool:
            # Defensive on None: the column is NOT NULL with a "fresh" default
            # and every write path passes it, but the old expression guarded
            # its input and dropping the guard would turn a hand-written row
            # into a stale one rather than a fresh one.
            return (row.freshness_status or "fresh") in STALE_STATUSES

        if page:
            freshness["confidence_score"] = page.confidence
            freshness["freshness_status"] = page.freshness_status
            freshness["is_stale"] = _is_stale(page)
        elif target_type == "symbol" and file_path_for_git:
            sym_page_id = f"file_page:{file_path_for_git}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page:
                freshness["confidence_score"] = sym_page.confidence
                freshness["freshness_status"] = sym_page.freshness_status
                freshness["is_stale"] = _is_stale(sym_page)
            else:
                freshness["confidence_score"] = None
                freshness["freshness_status"] = None
                freshness["is_stale"] = None
        else:
            freshness["confidence_score"] = None
            freshness["freshness_status"] = None
            freshness["is_stale"] = None
        result_data["freshness"] = freshness

    # --- KG layer + tour context (Phase 9) ---
    if target_type == "file" and file_path_for_git:
        kg_layers = await get_kg_layers(session, repo_id)
        if kg_layers:
            for _l in kg_layers:
                _l._parsed_node_ids = json.loads(_l.node_ids_json) if _l.node_ids_json else []
            file_layer = _find_layer_for_file(file_path_for_git, kg_layers)
            if file_layer:
                edge_res = await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.repository_id == repo_id,
                        GraphEdge.target_node_id == file_path_for_git,
                        GraphEdge.edge_type == "imports",
                    )
                )
                incoming_edges = list(edge_res.scalars())
                result_data["architectural_layer"] = {
                    "name": file_layer.name,
                    "description": (file_layer.description or "")[:200],
                    "role": _classify_file_role(file_path_for_git, file_layer, incoming_edges),
                }

            kg_tour = await get_kg_tour_steps(session, repo_id)
            if kg_tour:
                for _s in kg_tour:
                    _s._parsed_node_ids = json.loads(_s.node_ids_json) if _s.node_ids_json else []
                file_tour_step = _find_tour_step_for_file(file_path_for_git, kg_tour)
                if file_tour_step:
                    result_data["tour_context"] = {
                        "step": file_tour_step.step_order,
                        "title": file_tour_step.title,
                        "why": (file_tour_step.description or "")[:200],
                    }

    # --- Callers / Callees (replaces get_callers_callees) ---
    want_callers = bool(include and "callers" in include)
    want_callees = bool(include and "callees" in include)
    if want_callers or want_callees:
        await _resolve_call_graph(
            session,
            repository,
            target,
            target_type,
            result_data,
            want_callers=want_callers,
            want_callees=want_callees,
            exclude_spec=exclude_spec,
        )

    # --- Metrics (replaces get_graph_metrics) ---
    if include and "metrics" in include:
        await _resolve_metrics(session, repository, target, result_data)

    # --- Community (replaces get_community) ---
    if include and "community" in include:
        await _resolve_community(
            session, repository, target, result_data, exclude_spec=exclude_spec
        )

    # --- Code health (Phase 2) ---
    if include and "health" in include:
        await _resolve_health(session, repository, target, target_type, result_data)

    # --- Skeleton (distill) — opt-in only, see the module note ---
    if want_skeleton:
        await _resolve_skeleton(
            session, repository, target, target_type, result_data, repo_root=repo_root
        )

    return result_data
