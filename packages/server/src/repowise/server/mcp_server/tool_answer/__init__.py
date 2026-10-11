"""get_answer MCP tool — RAG-style synthesis over the wiki layer.

Façade: the tool used to be a single 1356-line ``tool_answer.py``. It is now
a package (orchestrator + retrieval / symbols / synthesis / confidence /
config). Importers keep working unchanged — ``from ...tool_answer import
get_answer`` still resolves, and importing this package registers the tool
via the ``@mcp.tool()`` decorator in ``answer``.
"""

from __future__ import annotations

from repowise.server.mcp_server.tool_answer.answer import get_answer

__all__ = ["get_answer"]
