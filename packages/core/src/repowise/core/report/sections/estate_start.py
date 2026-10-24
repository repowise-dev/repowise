"""Where a team new to the estate starts: the services most others depend on, and in
each repo the central files with the least documentation."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    capped_names,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import evidence_note, missing_graph, unread

_SERVICES = (
    "the stored system graph: for each service, the services a change to it reaches over "
    "structural edges (at most 3 hops), as the blast radius walks them"
)
_FILES = (
    "each index's file graph: production code files the health index scored (as the health "
    "and git indexes class them), ranked by PageRank centrality within their own repo, the "
    "least documented first (words on the file's written page; a page rendered from the code "
    "alone counts as none), as the knowledge map's onboarding targets; centrality is not "
    "compared across repos"
)
#: Files listed per repo in the text; the JSON keeps the full shortlist.
_PER_REPO = 3


async def _targets(session: Any, repository_id: str) -> list[dict[str, Any]]:
    """The knowledge map's onboarding targets for one repo, words counted exactly."""
    from sqlalchemy import func, select

    from repowise.core.analysis.knowledge_map import onboarding_targets
    from repowise.core.persistence.crud import code_file_rows
    from repowise.core.persistence.models import GitMetadata, GraphNode, HealthFileMetric, Page

    # Production code only, as the health index classed it, and a code file to
    # the git index: a Dockerfile, a README or a vendored library is central in
    # the graph and undocumented by nature, and would lead the list.
    nodes = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank)
            .join(
                HealthFileMetric,
                (HealthFileMetric.repository_id == GraphNode.repository_id)
                & (HealthFileMetric.file_path == GraphNode.node_id),
            )
            .join(GitMetadata, GitMetadata.file_path == GraphNode.node_id)
            .where(
                code_file_rows(repository_id),
                GraphNode.repository_id == repository_id,
                GraphNode.node_type == "file",
                GraphNode.is_test.is_(False),
                GraphNode.pagerank > 0.0,
                HealthFileMetric.code_origin == "production",
            )
        )
    ).all()
    # Written pages only: a page rendered from the code alone documents nothing
    # the file does not say. Character length ranks like words; exact words are
    # read for the shortlist only.
    written = (
        Page.repository_id == repository_id,
        Page.page_type == "file_page",
        Page.provider_name != "template",
    )
    sizes = {
        path: int(n or 0)
        for path, n in await session.execute(
            select(Page.target_path, func.length(Page.content)).where(*written)
        )
    }
    shortlist = onboarding_targets(nodes, sizes)
    documented = [c["path"] for c in shortlist if c["doc_words"] > 0]
    words: dict[str, int] = {}
    if documented:
        words = {
            path: len((content or "").split())
            for path, content in await session.execute(
                select(Page.target_path, Page.content).where(
                    *written, Page.target_path.in_(documented)
                )
            )
        }
    ranked = [{**c, "doc_words": words.get(c["path"], 0)} for c in shortlist]
    # Re-ranked on exact words, the fold's own key: characters only made the cut.
    return sorted(ranked, key=lambda c: (c["doc_words"], -c["pagerank"]))


def _services(ctx: WorkspaceReportContext, graph: Any, reach: dict[str, int]) -> dict[str, Any]:
    evidence = ctx.evidence()
    if evidence.state == "insufficient":
        return check(
            "start-services",
            reason=f"not determined: {evidence.summary}",
            method=_SERVICES,
            basis="unresolved",
        )
    names = {n.id: n for n in graph.nodes}
    ranked = sorted((n for n in reach if reach[n]), key=lambda n: (-reach[n], n))
    leaves = sorted(n for n in reach if not reach[n])
    details = capped(
        [f"{n} ({names[n].kind}): a change reaches {reach[n]} service(s)" for n in ranked], limit=10
    )
    if leaves:
        details.append(f"no other service depends on: {capped_names(leaves)}")
    return check(
        "start-services",
        reason=(
            f"{len(ranked)} of {len(reach)} service(s) have other services depending on them; "
            "read the widest first" + evidence_note(evidence)
            if ranked
            else "no service has another depending on it" + evidence_note(evidence)
        ),
        details=details,
        method=_SERVICES,
        basis="inferred",
        data={"reach": dict(sorted(reach.items()))},
    )


def _files(
    ctx: WorkspaceReportContext, targets: dict[str, Any], repo_reach: dict[str, int]
) -> dict[str, Any]:
    read = {a: t for a, t in targets.items() if not isinstance(t, Exception)}
    order = sorted(read, key=lambda a: (-repo_reach.get(a, 0), a))
    lines = []
    for alias in order:
        files = read[alias][:_PER_REPO]
        if files:
            lines.append(
                f"{alias}: "
                + ", ".join(
                    f"{f['path']} ({f['doc_words']} doc words)" if f["doc_words"] else f"{f['path']} (undocumented)"
                    for f in files
                )
            )
    empty = [a for a in order if not read[a]]
    details = capped(lines)
    if empty:
        details.append(f"no ranked file in the index: {capped_names(empty)}")
    if failed := unread(targets):
        details.append(f"index could not be read: {capped_names(failed)}")
    not_indexed = [e.get("alias") or e.get("name") for e in ctx.repos if not e.get("indexed", True)]
    if not_indexed:
        details.append(f"not indexed: {capped_names(not_indexed)}")
    return check(
        "start-files",
        reason=(
            f"the most central, least documented files of {len(lines)} repo(s), "
            "repos ordered by how far a change to their services reaches"
            if lines
            else "no repo index holds a ranked file graph"
        ),
        details=details,
        method=_FILES,
        basis="inferred" if lines else "unresolved",
        data={"repositories": {a: read[a] for a in order}},
    )


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    from repowise.core.workspace.seams import service_reach

    graph = ctx.system_graph()
    if graph is None:
        return missing_graph("start-services", _SERVICES)
    reach = service_reach(graph)
    repo_reach: dict[str, int] = {}
    for node in graph.nodes:
        repo_reach[node.repo] = max(repo_reach.get(node.repo, 0), reach[node.id])
    targets = await ctx.each_index("onboarding_targets", _targets)
    return [_services(ctx, graph, reach), _files(ctx, targets, repo_reach)]


report_sections.register(
    "estate-start",
    Section(
        title="Where to start",
        summary="The services most others depend on, then each repo's central, least documented files.",
        collect=collect,
        order=60,
        report="estate",
        scope="workspace",
    ),
)
