"""MCP Tool: get_symbol — byte-precise source retrieval for a single symbol.

This is the structural counterpart to get_context. Where get_context returns
file-level narrative (summary, symbol list, importers), get_symbol returns
the actual source bytes of one named symbol — function body, class body, or
method — by slicing the on-disk source file using the line range stored on
the WikiSymbol row at index time.

Why a separate tool instead of "include source" on get_context?
  * Granularity: a single function is ~30 lines vs a 300-line file. Cuts the
    cached prompt prefix by ~10x when the agent only needs one symbol.
  * Predictability: response size is bounded by the symbol size, never the
    file size — no surprise 50 KB payloads.
  * Verified bounds: the persisted line range is checked against the live
    file before serving (name-on-definition-line gate); on mismatch the
    file is re-parsed once, the bounds corrected and healed in the DB. A
    ``verified: true`` response never needs a follow-up Read.

The tool is intentionally additive — get_context remains the right call for
"explain this file" or "what's the relationship between A and B" questions.
get_symbol is for "show me the body of this function".

Resolution strategy (in order):
  1. Exact match on WikiSymbol.symbol_id (the canonical "{path}::{name}" key)
  2. Exact match on (file_path, qualified_name) — supports class.method form
  3. Exact match on (file_path, name) — supports unqualified names
  4. Suffix match — a bare filename or partial path ("answer.py::get_answer")
     resolves against any file whose path ends with that segment, on the leaf
     name; mirrors get_context's basename ladder. A total miss returns
     ``suggestions`` (real path-qualified ids) instead of a bare "not found".

The tool also resolves **omission refs**: a ``symbol_id`` of the form
``"repowise#<12-hex>"`` (from a ``[repowise#<ref>: ...]`` truncation marker)
retrieves the omitted content from the durable omission store instead of the
symbol index. The two id shapes are syntactically unambiguous — a real
symbol_id always contains a file path — so dispatch is trivial and the
symbol contract is untouched.

Returns a flat dict (not wrapped in `targets`) so the agent can pipe the
`source` field straight to its scratch buffer.

``tool.py`` holds the registered tool; the sibling modules hold its stages.
Every name the single module had is re-exported here, and a stage calls a
name tests patch through the package (``_pkg.<name>``), never by bare name,
or the patch stops reaching it.
"""

# ruff: noqa: F401

from __future__ import annotations

import functools
import json
import re
import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import register_post_shed
from repowise.server.mcp_server._budget.contracts import DEFAULT_RESPONSE_CHARS
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _is_workspace_mode,
    _resolve_repo_context,
    _unsupported_repo_all,
    clean_signature,
    is_excluded,
    read_repo_file_text,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import completeness_line as _completeness_line
from repowise.server.mcp_server._meta import symbol_hint as _symbol_hint
from repowise.server.mcp_server._references import (
    omission_reference,
    path_identity,
    source_reference,
    symbol_identity,
)
from repowise.server.mcp_server._symbol_lookup import (
    NAME_SEPARATORS,
    bare_name,
    name_variants,
    order_candidates,
    parse_symbol_id,
    resolve_symbol_rows,
    symbol_id_variants,
)
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
    heal_symbol_row,
    parse_live_symbols,
)
from repowise.server.mcp_server._workspace_routing import (
    hint_misses,
    needs_routing,
    other_repo_roots,
    route_targets,
)
from repowise.server.mcp_server.tool_symbol.ambiguous import (
    _AMBIGUITY_CHAR_BUDGET,
    _correct_ambiguity_note,
    _finish_ambiguous,
    _render_ambiguous,
)
from repowise.server.mcp_server.tool_symbol.body import (
    _CONTAINER_KINDS,
    _OUTLINE_HEADER_LINES,
    _OUTLINE_MEMBER_CHARS,
    _OUTLINE_MIN_CHARS,
    _OUTLINE_SUMMARY_CHARS,
    _container_outline,
    _note_continuation,
    _note_outline,
    _outline_if_large,
    _serve_symbol,
    _served_body,
    _ServedBody,
    _symbol_body_response,
    _unreadable_source_response,
)
from repowise.server.mcp_server.tool_symbol.callees import (
    _CALLEE_CHAR_BUDGET,
    _MAX_CALLEE_BODY_LINES,
    _MAX_CALLEE_DEPTH,
    _MAX_CALLEES_PER_HOP,
    _CalleeBody,
    _CalleeHop,
    _discover_callee_hops,
    _expand_callees,
    _partition_callees,
    _prepare_callee_entries,
    _render_callee,
    _slice_callee_body,
    _spend_callee_budget,
    _unseen_callees_of,
)
from repowise.server.mcp_server.tool_symbol.lookup import (
    _NAME_SCAN_CAP,
    _NAME_SEPARATORS,
    _bare_name,
    _name_variants,
    _order_candidates,
    _parse_symbol_id,
    _resolve_symbol,
    _rows_in_scope,
    _symbol_id_variants,
    _symbol_suggestions,
    _symbols_named,
)
from repowise.server.mcp_server.tool_symbol.misses import (
    _COMMENT_PREFIXES,
    _DEF_KEYWORDS,
    _DEF_MODIFIERS,
    _MAX_FALLBACK_MATCHES,
    _live_grep_fallback,
    _live_grep_response,
    _not_found_response,
)
from repowise.server.mcp_server.tool_symbol.omission import (
    _extract_omission_ref,
    _resolve_omission_ref,
)
from repowise.server.mcp_server.tool_symbol.ranges import (
    _MAX_RANGE_LINES,
    _RANGE_ID_RE,
    _range_id_read,
    _resolve_range_read,
)
from repowise.server.mcp_server.tool_symbol.text import (
    _MAX_SOURCE_LINES,
    _elapsed_ms,
    _number_lines,
    _read_file_text,
    _slice_text,
)
from repowise.server.mcp_server.tool_symbol.tool import (
    _indexed_symbol_read,
    _reference_arguments,
    _requested_id,
    _routed_read,
    get_symbol,
)
