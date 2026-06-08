"""MCP savings instrumentation — counterfactual token-savings per tool.

Keeps the savings concern (measuring what each tool answer replaced, recording
``mcp:<tool>`` rows into the unified ledger) out of the generic
:class:`~repowise.core.registry.mcp_tool_registry.MCPToolRegistry`. The server
passes :func:`instrument` into ``apply(mcp, middleware=instrument)`` at boot;
core stays decoupled from this package.
"""

from __future__ import annotations

from .counterfactual import replaced_tokens_for
from .recorder import record_mcp_saving
from .wrapper import declare_replaced, instrument

__all__ = [
    "declare_replaced",
    "instrument",
    "record_mcp_saving",
    "replaced_tokens_for",
]
