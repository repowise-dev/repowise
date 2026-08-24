"""Single source for the agent-facing MCP tool table.

Rendered into CLAUDE.md / AGENTS.md by the editor-file templates. Keyed by
tool name so a drift test can assert every row names a registered MCP tool
(and every default-surface tool has a row) — the table used to be hand-edited
prose in the template and silently drifted from the live registry.

Row style: one entry per tool, **one sentence plus at most one clause**,
leading with when to call it. The load-bearing response fields (symbol_bodies,
verified, continuation, directive, sources) stay named, because a row that
drops them costs the agent a round trip to discover them. Reference detail
lives in docs/agent/MCP_TOOLS.md, not here.

**A row is an advertisement, and a granular tool advertised as a peer gets
used granularly.** Where a short capability list named ``get_symbol`` beside
``get_answer``, agents spent the large majority of their retrieval calls on it
and finished having made *more* tool calls than an agent with no tools at all:
a per-symbol tool supplements navigation instead of replacing it, one symbol
per call. It is also the one tool whose payload cannot be trimmed — a trimmed
``get_symbol`` is 99% of a full one — so its calls are the ones no size work
can reach. Given the same surface with no such list, agents reached for
``get_answer`` instead and called ``get_symbol`` not at all.

So the ``get_symbol`` row leads by saying what it is NOT, and the
``get_answer`` and ``get_context`` rows say where bodies do come from. **This
is about naming, not about count**: harnesses that defer tool schemas read
this table and nothing else until they search, so serving fewer tools changes
nothing an agent sees. Do not "fix" it by shortening the row back to a neutral
capability description, and do not "fix" it by dropping the tool.

**Length is the constraint, not the feature list.** This table is resident in
the prompt prefix of every session in every repo repowise has indexed, so it is
re-read on every API call: measured at 50.4x on one corpus, which makes a
character here roughly fifty times more expensive than a character in a tool
response. It was 3,402 characters and bought a repowise MCP call before ~1.0%
of edits under Claude Code.

It is kept, and kept short, because the table is what reaches an agent under a
harness that defers MCP schemas. Claude Code defers, so an agent there must
issue a ``ToolSearch`` before a tool is even a candidate, and this table in
CLAUDE.md is the only description it sees until then. Codex loads the schemas
up front and needs the table least, but reads the same AGENTS.md block, so the
rows must stay accurate for both. Adding a sentence here is a real cost; spend
it in the tool's own schema description instead, which is paid only on use.
Budgets in ``tests/unit/server/mcp/test_tool_table_drift.py``.
"""

from __future__ import annotations

# Tool name -> (signature shown in the table, agent-facing row text).
TOOL_TABLE_ROWS: dict[str, tuple[str, str]] = {
    "get_answer": (
        "get_answer(question)",
        # `degraded` earns its three words because without them the row is wrong
        # for a whole class of install. An LLM-less repowise answers every
        # question with `confidence: "low"`, since confidence rates prose that
        # was never synthesised, and this row told the agent to distrust the
        # payload on the strength of it, so it re-searched after every call.
        # `retrieval_quality` is the field that rates what such a payload does
        # carry. Reworded, not lengthened: 186 chars against the 179 it replaced.
        'First call for any how/where/why question. Cite `confidence: "high"` or '
        '`grounding: "extracted"` directly; `degraded` means judge by '
        "`retrieval_quality`. `symbol_bodies` has live bodies.",
    ),
    "get_context": (
        "get_context(targets=[...])",
        "Triage card for files/modules/symbols: docs, signatures, hotspot, fix "
        'history. No source bytes — `include=["skeleton"]` for the whole file '
        'verified, `["callers"|"decisions"]` for depth. Batch targets.',
    ),
    "get_symbol": (
        "get_symbol(id, depth?)",
        "**Follow-up, not an entry point** — one verified body for an id a prior "
        "response named (`path.py::Name`, `path.py:140-180`, `repowise#<hex>`). Never "
        "walk a file symbol by symbol; Read it.",
    ),
    "search_codebase": (
        "search_codebase(query)",
        "Hybrid search, auto-routed by query shape; force with "
        "`mode=symbol|path|concept|hybrid`. A hit whose `sources` are `[fts]` only "
        "has no semantic agreement, so verify it.",
    ),
    "get_why": (
        "get_why(query, targets?)",
        "Why the code is shaped this way: decision records, git archaeology, "
        "rationale comments. Call before a refactor or a pattern divergence.",
    ),
    "get_risk": (
        "get_risk(targets, changed_files?, include?)",
        "File history and structural reach. PR mode leads with `directive`; its "
        "0-10 structural heuristic is uncalibrated, not a probability. Read typed "
        "test recommendations and coverage state first.",
    ),
    "get_change_risk": (
        "get_change_risk(revspec?, extensions?, exclude_patterns?)",
        "Deterministic live-diff review signal for a commit or range. Lead with "
        "benchmarked percentile/classification; the 0-10 diff-shape score is "
        "supporting, not a probability. `get_risk` scores paths.",
    ),
    "get_health": (
        "get_health(targets?, include?)",
        "Defect / maintainability / performance scores and findings. Self-check the "
        "files you touched before finishing.",
    ),
    "get_dead_code": (
        "get_dead_code(tier?, min_confidence?, safe_only?)",
        "Confidence-tiered unreachable files / unused exports / zombie packages. For "
        "cleanup sweeps, not targeted fixes.",
    ),
    "get_overview": (
        "get_overview()",
        "Architecture map. Call once, first, in an unfamiliar repo; skip it after that.",
    ),
}


def render_tool_table() -> str:
    """Markdown table of the tool rows, in the dict's curated order."""
    lines = ["| Tool | When and why |", "|------|--------------|"]
    for signature, row in TOOL_TABLE_ROWS.values():
        lines.append(f"| `{signature}` | {row} |")
    return "\n".join(lines)
