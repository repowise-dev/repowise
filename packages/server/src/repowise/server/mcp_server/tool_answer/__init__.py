"""get_answer MCP tool — RAG-style synthesis over the wiki layer.

Façade: the tool used to be a single 1356-line ``tool_answer.py``. It is now
a package (orchestrator + retrieval / symbols / synthesis / confidence /
config). Importers keep working unchanged — ``from ...tool_answer import
get_answer`` still resolves, and reading that name imports ``answer``, which
registers the tool via the ``@mcp.tool()`` decorator.

``get_answer`` binds on first attribute access, not at import: a leaf such as
``tool_answer.confidence`` initialises this package first, and an eager import
made it pay for the orchestrator, persistence and SQLAlchemy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from repowise.server.mcp_server.tool_answer.answer import get_answer

__getattr__, __dir__ = lazy_exports(__name__, {"get_answer": ".answer"}, globals())

__all__ = ["get_answer"]
