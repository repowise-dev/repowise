"""Output-size budgeting for get_context — thin shim over the shared budgeter.

The staged truncation strategy that used to live here is now the
reference implementation for ALL tools and lives in
``repowise.server.mcp_server._budget``. This module keeps the historical
names importable (tests and older callers import ``_truncate_to_budget`` and
friends from here) and adds nothing of its own.
"""

from __future__ import annotations

from repowise.server.mcp_server._budget.budgeter import (
    CHAR_BUDGET as _CHAR_BUDGET,
)
from repowise.server.mcp_server._budget.budgeter import (
    CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
)
from repowise.server.mcp_server._budget.budgeter import (
    HEAVY_DOC_FIELDS as _HEAVY_DOC_FIELDS,
)
from repowise.server.mcp_server._budget.budgeter import (
    TOKEN_BUDGET as _TOKEN_BUDGET,
)
from repowise.server.mcp_server._budget.budgeter import (
    estimate_response_tokens as _estimate_tokens,
)
from repowise.server.mcp_server._budget.budgeter import (
    query_terms_for as _query_terms_for,
)
from repowise.server.mcp_server._budget.budgeter import (
    symbol_priority as _symbol_priority,
)
from repowise.server.mcp_server._budget.budgeter import (
    truncate_to_budget as _truncate_to_budget,
)

__all__ = [
    "_CHARS_PER_TOKEN",
    "_CHAR_BUDGET",
    "_HEAVY_DOC_FIELDS",
    "_TOKEN_BUDGET",
    "_estimate_tokens",
    "_query_terms_for",
    "_symbol_priority",
    "_truncate_to_budget",
]
