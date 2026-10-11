"""Surface taxonomy for savings sources.

Every savings/omission row carries a ``source`` string. Distill writes
``cli`` / ``hook-bash`` / ``hook-powershell`` / ``hook-codex``; the MCP server
writes ``mcp:<tool>``. Callers that split totals into a *distill* block and an
*mcp* block route the source through these two helpers so the prefix convention
lives in exactly one place.
"""

from __future__ import annotations

#: Prefix every MCP-originated source carries (``mcp:get_risk`` etc.).
MCP_PREFIX = "mcp:"


def is_mcp(source: str) -> bool:
    """True when *source* came from an MCP tool (``mcp:<tool>``)."""
    return source.startswith(MCP_PREFIX)


def surface_of(source: str) -> str:
    """Coarse surface for *source* — ``"mcp"`` or ``"distill"``.

    Everything that is not an ``mcp:`` source is distill (CLI command or any
    of the rewrite-hook shells), so the hero card can group the ledger into
    two honest buckets.
    """
    return "mcp" if is_mcp(source) else "distill"
