"""MCP Tool 8: get_architecture_diagram — Mermaid diagram generation."""

from __future__ import annotations

import re

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._server import mcp


def _sanitize_mermaid_id(node_id: str) -> str:
    """Replace all non-alphanumeric/non-underscore chars with underscore."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", node_id)


def _short_label(node_id: str) -> str:
    """Shorten a full path to at most parent/filename for readable diagram labels.

    ``packages/core/src/repowise/core/persistence/models.py``
    → ``persistence/models.py``
    """
    parts = node_id.replace("\\", "/").split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


@mcp.tool()
async def get_architecture_diagram(
    scope: str = "repo",
    path: str | None = None,
    diagram_type: str = "auto",
    show_heat: bool = False,
    repo: str | None = None,
) -> dict:
    """Get a Mermaid diagram for the codebase or a specific module.

    Args:
        scope: "repo", "module", or "file".
        path: Module or file path (required for module/file scope).
        diagram_type: "auto", "flowchart", "class", or "sequence".
        show_heat: Annotate nodes with churn heat colors (red=hot, yellow=warm, green=cold).
        repo: Repository path, name, or ID.
    """
    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)

        if scope == "repo":
            # Return the architecture diagram page
            result = await session.execute(
                select(Page).where(
                    Page.repository_id == repository.id,
                    Page.page_type == "architecture_diagram",
                )
            )
            page = result.scalar_one_or_none()
            if page:
                return {
                    "diagram_type": diagram_type if diagram_type != "auto" else "flowchart",
                    "mermaid_syntax": page.content,
                    "description": page.title,
                }

        # For module/file scope or fallback, build diagram from graph
        filter_prefix = path or ""

        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_id.like(f"{filter_prefix}%")
                if filter_prefix
                else GraphNode.repository_id == repository.id,
            )
        )
        nodes = result.scalars().all()

        result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repository.id,
            )
        )
        edges = result.scalars().all()

        node_ids = {n.node_id for n in nodes}
        pr_map = {n.node_id: n.pagerank for n in nodes}
        relevant_edges = sorted(
            [e for e in edges if e.source_node_id in node_ids or e.target_node_id in node_ids],
            key=lambda e: pr_map.get(e.source_node_id, 0.0),
            reverse=True,
        )

        # Load git churn data for heat map
        churn_map: dict[str, float] = {}
        if show_heat:
            git_res = await session.execute(
                select(GitMetadata.file_path, GitMetadata.churn_percentile).where(
                    GitMetadata.repository_id == repository.id,
                )
            )
            churn_map = {row[0]: row[1] for row in git_res.all()}

        # Build Mermaid flowchart
        lines = ["graph TD"]
        seen_nodes: set[str] = set()
        node_classes: dict[str, str] = {}  # mermaid_id → class

        # For module-scoped diagrams, clip cross-boundary nodes to a single
        # "[external]" stub so the diagram stays focused on the target module.
        external_id = "external_deps"
        _external_added = False

        for e in relevant_edges[:50]:  # Limit to 50 edges for readability
            src_id = e.source_node_id
            tgt_id = e.target_node_id

            # Clip nodes outside the module boundary
            src_external = bool(filter_prefix and not src_id.startswith(filter_prefix))
            tgt_external = bool(filter_prefix and not tgt_id.startswith(filter_prefix))

            # Skip edges where both ends are external
            if src_external and tgt_external:
                continue

            if src_external:
                src = external_id
                src_label = "external"
            else:
                src = _sanitize_mermaid_id(src_id)
                src_label = _short_label(src_id)

            if tgt_external:
                tgt = external_id
                tgt_label = "external"
            else:
                tgt = _sanitize_mermaid_id(tgt_id)
                tgt_label = _short_label(tgt_id)

            # Skip self-loops created by clipping
            if src == tgt:
                continue

            if src not in seen_nodes:
                if src == external_id:
                    if not _external_added:
                        lines.append(f'    {external_id}[/"external"/]')
                        _external_added = True
                        node_classes[external_id] = "ext"
                else:
                    lines.append(f'    {src}["{src_label}"]')
                    if show_heat:
                        pct = churn_map.get(src_id, 0.0)
                        node_classes[src] = (
                            "hot" if pct >= 0.75 else ("warm" if pct >= 0.4 else "cold")
                        )
                seen_nodes.add(src)

            if tgt not in seen_nodes:
                if tgt == external_id:
                    if not _external_added:
                        lines.append(f'    {external_id}[/"external"/]')
                        _external_added = True
                        node_classes[external_id] = "ext"
                else:
                    lines.append(f'    {tgt}["{tgt_label}"]')
                    if show_heat:
                        pct = churn_map.get(tgt_id, 0.0)
                        node_classes[tgt] = (
                            "hot" if pct >= 0.75 else ("warm" if pct >= 0.4 else "cold")
                        )
                seen_nodes.add(tgt)

            lines.append(f"    {src} --> {tgt}")

        # Apply heat + external classes
        if node_classes:
            for nid, cls in node_classes.items():
                lines.append(f"    class {nid} {cls}")
            if show_heat:
                lines.append("    classDef hot fill:#ff6b6b,color:#000")
                lines.append("    classDef warm fill:#ffd93d,color:#000")
                lines.append("    classDef cold fill:#6bcb77,color:#000")
            if _external_added:
                lines.append("    classDef ext fill:#ccc,stroke-dasharray:5 5,color:#666")

        mermaid = "\n".join(lines) if len(lines) > 1 else "graph TD\n    A[No graph data available]"

        return {
            "diagram_type": diagram_type if diagram_type != "auto" else "flowchart",
            "mermaid_syntax": mermaid,
            "description": f"Dependency graph for {scope}: {path or 'entire repo'}"
            + (" (with churn heat map)" if show_heat else ""),
        }
