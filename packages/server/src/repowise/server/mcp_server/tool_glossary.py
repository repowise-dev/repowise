"""MCP Tool: get_glossary — the repository's canonical vocabulary, as data.

The prescriptive half of the glossary (issue #2262), served as a tool. The
mined vocabulary already reaches an agent as the rendered ``onboarding/glossary``
page id behind ``get_overview(include=["outline"])``, which answers "what does
this repository call things" for a reader. It does not answer the question an
agent acting on the repository actually has: *which* of these words is the one
to use, which are the ones the team ruled out, and where is the term used.

That answer is a file the team authored — a root ``CONTEXT.md`` in the
``grill-with-docs`` format, a ``GLOSSARY.md``, or the per-context files a
``CONTEXT-MAP.md`` names. This tool reads it plus the house vocabulary the
miner already produces, and returns both as rows with ``status``, ``avoid``,
``context``, ``source_path`` and ``used_in``.

Opt-in (``mcp.tools: ["+get_glossary"]``), not a twelfth flagship: most
repositories have not declared a glossary, and a tool that answers "nothing
here" on every call is not worth a schema in every session's prompt prefix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.generation.concept_tree.vocabulary import HouseTerm, extract_house_terms
from repowise.core.generation.declared_glossary import (
    DeclaredTerm,
    load_declared_glossary,
    phrase_key,
)
from repowise.core.generation.house_vocabulary import SelectedTerm, select_terms
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: Rows served before the tail is dropped. A lookup surface, so a reader scans
#: for one row; passing a bigger ``limit`` serves more of the same list.
_DEFAULT_LIMIT = 40
_MAX_LIMIT = 200
#: How many paths to name per row under ``used_in``. The count stays in full.
_MAX_USED_IN = 5
#: Module pages read for the corroboration corpus. A module group is what the
#: structure calls a part of the system, and its title plus its summary is what
#: makes a mined term's corroboration mean something. Bounded because this is a
#: store read on the request path.
_MAX_CORROBORATION_PAGES = 400


def _row(
    term: SelectedTerm,
    *,
    declared_by_key: dict[str, DeclaredTerm],
) -> dict[str, Any]:
    """One served row: the canonical word, or the used one."""
    declared = declared_by_key.get(phrase_key(term.term))
    row: dict[str, Any] = {
        "term": term.term,
        "status": term.status,
        "definition": (declared.definition if declared else term.definition),
        "avoid": list(declared.avoid) if declared else [],
        "context": declared.context if declared else None,
        "source_path": declared.source_path if declared else term.source_path,
        "used_in": list(term.corroborating_names[:_MAX_USED_IN]),
        "used_in_count": len(term.corroborating_names),
    }
    if declared is not None:
        # 1-based, so a reader can open the file at the line rather than at the
        # document.
        row["source_line"] = declared.line
    if term.demoted_by:
        row["demoted_by"] = term.demoted_by
        row["demoted_reason"] = (
            f"the declared glossary marks this word avoid; "
            f'use "{term.demoted_by}"'
        )
    if term.status == "declared" and not term.corroborating_names:
        row["note"] = "not yet in code: declared by the team, used by no module group"
    return row


def _matches_targets(row: dict[str, Any], targets: list[str]) -> bool:
    """Whether a row's ``used_in`` intersects *targets*, by substring either way.

    Substring rather than equality because a caller passes what they have:
    ``src/analysis`` as a prefix, or a module group's human title. Both are
    honest readings of "does this term belong in what I am working on".
    """
    used = [str(name).lower() for name in row.get("used_in") or []]
    if not used:
        return False
    for target in targets:
        needle = target.strip().lower()
        if not needle:
            continue
        if any(needle in name or name in needle for name in used):
            return True
    return False


@mcp.tool(
    default=False,
    surface_order=225,
    trust_kind="user",
    artifact_type="glossary",
    presentation="glossary",
    evidence_basis="measured",
)
async def get_glossary(
    targets: list[str] | None = None,
    context: str | None = None,
    repo: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    """What this repository calls things, and the words the team ruled out.

    Returns the terms a glossary the team authored declares canonical first,
    with the synonyms to avoid, the bounded context each belongs to and the
    line it was written on, followed by the terms mined from the repository's
    own documents. A declared term outranks a mined one that spells the same
    phrase; a mined term the declared glossary marks avoided is kept and
    demoted, with ``demoted_by`` naming the word to use instead.

    Read this before naming a concept in code, a doc, an issue or a commit
    message: it is the difference between writing what the team calls a thing
    and writing what a linter or a model would have guessed.

    Args:
        targets: Optional paths or module names. Keeps only terms whose
            ``used_in`` intersects them, so a question about one subsystem gets
            that subsystem's vocabulary.
        context: Optional bounded-context label (from a ``CONTEXT-MAP.md``)
            to restrict to one context's declared terms.
        repo: Repository alias, path, or ID.
        limit: Maximum rows served. Default 40; the tail is dropped with a
            count, never silently.
    """
    if repo == "all":
        return _unsupported_repo_all("get_glossary")

    ctx = await _resolve_repo_context(repo)
    root = Path(ctx.path) if ctx.path else None
    if root is None or not root.is_dir():
        return {
            "error": "no repository path to read a glossary from",
            "_meta": _build_meta(),
        }

    declared = _load_declared(root, context=context)
    house_terms = _mine(root)
    module_names = await _module_corroboration(ctx.session_factory)
    selected = select_terms(house_terms, module_names, declared=declared)

    declared_by_key = {phrase_key(term.term): term for term in declared}
    rows = [_row(term, declared_by_key=declared_by_key) for term in selected]

    if targets:
        wanted = [t for t in targets if t and t.strip()]
        rows = [row for row in rows if _matches_targets(row, wanted)]

    total = len(rows)
    served = rows[: max(1, min(limit, _MAX_LIMIT))]
    declared_rows = [row for row in served if row["status"] == "declared"]

    result: dict[str, Any] = {
        "terms": served,
        "declared_count": sum(1 for row in rows if row["status"] == "declared"),
        "mined_count": sum(1 for row in rows if row["status"] == "mined"),
        "demoted_count": sum(1 for row in rows if row.get("demoted_by")),
        "total": total,
        "emitted": len(served),
        "_meta": _build_meta(
            targets=targets or None,
            hint=(
                "A declared term is the team's ruling on naming; prefer it over "
                "any synonym in `avoid`."
                if declared_rows
                else (
                    "No glossary is declared in this repository, so these are "
                    "mined from its own documents. Add a root CONTEXT.md to make "
                    "the terms authoritative."
                )
            ),
        ),
    }
    if total > len(served):
        result["truncated"] = True
        result["reduced_reason"] = "limit"
    if not declared_rows:
        result["declared_glossary"] = {
            "present": False,
            "note": (
                "No CONTEXT.md, GLOSSARY.md or docs/GLOSSARY.md in this "
                "repository, and no CONTEXT-MAP.md naming one. The rows below "
                "are the mined house vocabulary: what the repository says its "
                "words mean, not what the team decided they mean."
            ),
        }
    return result


def _load_declared(root: Path, *, context: str | None) -> list[DeclaredTerm]:
    """The declared glossary, filtered to one context when asked. Never raises."""
    try:
        declared = load_declared_glossary(root)
    except Exception:
        return []
    if not context:
        return declared
    wanted = context.strip().lower()
    return [
        term
        for term in declared
        if term.context is not None and term.context.strip().lower() == wanted
    ]


def _mine(root: Path) -> list[HouseTerm]:
    """The house vocabulary for this repository. Never raises."""
    try:
        return extract_house_terms(root)
    except Exception:
        return []


async def _module_corroboration(session_factory: Any) -> list[str]:
    """What the structural side calls the parts of the system, from the store.

    One string per module page — its title, plus its summary. A module group is
    cut from the dependency graph and named from the code, so a mined term
    appearing in one was arrived at twice, from the documents and from the
    structure, independently. This is the same corroboration the generated
    overview and glossary pages select on; reading it here is what keeps the
    tool's answer identical to the page's rather than a second opinion about
    which mined terms count.

    Never raises: a store that cannot answer costs the mined rows their
    corroboration, and the declared rows keep theirs regardless.
    """
    if session_factory is None:
        return []
    try:
        async with get_session(session_factory) as session:
            repository = await _get_repo(session)
            result = await session.execute(
                select(Page.title, Page.summary)
                .where(
                    Page.repository_id == repository.id,
                    Page.page_type == "module_page",
                    Page.freshness_status != "tombstone",
                )
                .limit(_MAX_CORROBORATION_PAGES)
            )
            rows = result.tuples().all()
    except Exception:
        return []
    return [f"{title}\n{summary}" for title, summary in rows if title]
