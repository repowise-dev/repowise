"""Single source for the agent-facing MCP tool table.

Rendered into CLAUDE.md / AGENTS.md by the editor-file templates. Keyed by
tool name so a drift test can assert every row names a registered MCP tool
(and every default-surface tool has a row) — the table used to be hand-edited
prose in the template and silently drifted from the live registry.

Row style: one entry per tool, 1-3 sentences, leading with when to call it.
The load-bearing response fields (symbol_bodies, verified, continuation,
directive, search_method) stay named — they are what the trust protocol keys
on. Reference detail lives in docs/MCP_TOOLS.md, not here.
"""

from __future__ import annotations

# Tool name -> (signature shown in the table, agent-facing row text).
TOOL_TABLE_ROWS: dict[str, tuple[str, str]] = {
    "get_answer": (
        "get_answer(question)",
        'First call for any how / where / why question. `confidence: "high"` or '
        '`grounding: "extracted"` is content-grounded — cite it directly. When the '
        "question names an indexed symbol, `symbol_bodies` carries its full live body "
        "(skip the `get_symbol` follow-up). Low confidence returns `best_guesses` with "
        "one-line justifications plus `code_rationale` (rationale comments mined live "
        "from candidate source).",
    ),
    "get_context": (
        "get_context(targets=[...])",
        "Triage card for files/modules/symbols: summary, signatures, `symbol_id`s, "
        "`hotspot` bit. File targets auto-serve a `verified` skeleton (every signature "
        "at a fraction of a full Read); `mostly_full` marks files where Read costs "
        "little more. Batch targets in one call. Opt-in blocks: "
        '`include=["callers"|"callees"|"ownership"|"decisions"|"metrics"]`.',
    ),
    "get_symbol": (
        "get_symbol(id)",
        'One verified body: `"path.py::Name"` (indexed symbol), `"path.py:140-180"` '
        '(live range read), or `"repowise#<hex>"` (omission ref). Source arrives in '
        "Read's numbered format — treat it as an already-performed Read. `truncated` "
        "responses carry a `continuation` naming the exact next range; ambiguous ids "
        "return every match in `candidates`. Index misses fall back to live-grep "
        "`fallback_lines`.",
    ),
    "search_codebase": (
        "search_codebase(query)",
        "Hybrid search, auto-routed by query shape: identifier → symbol hits (pipe "
        "`symbol_id` into `get_symbol`), path → file pages, prose → wiki-semantic. "
        "Force with `mode=symbol|path|concept|hybrid`. Verify concept hits carrying "
        '`search_method: "bm25"`.',
    ),
    "get_why": (
        "get_why(query, targets?)",
        "Why the code is shaped this way: decision records with evidence and "
        "supersession lineage, falling back to git archaeology and `code_rationale` "
        "comments. Call before refactors or pattern divergences.",
    ),
    "get_risk": (
        "get_risk(targets, changed_files?)",
        "What history says about touching these files: churn, owners, co-change "
        "partners, blast radius. PR mode (`changed_files`) leads with a `directive` "
        "block — read `will_break` / `missing_cochanges` / `missing_tests` / "
        "`tests_to_run` first. `tests_to_run` is coverage-backed (the tests the "
        "per-test map proves exercise the changed files); empty means unknown, "
        "never no tests. To score a whole commit or diff range instead, use "
        "`get_change_risk`.",
    ),
    "get_change_risk": (
        "get_change_risk(revspec, extensions?, exclude_patterns?)",
        "Pre-merge defect score for a whole commit or `base..head` range, computed "
        "from its diff shape on the live checkout (no index, no LLM). Lead with "
        "`risk_percentile` (this change ranked against sampled recent commits), "
        "summarized by `review_priority` and `classification`; `score` / "
        "`probability` / `level` are the corpus-calibrated fallback. Distinct from "
        "`get_risk`, which scores indexed files by path. A `warning` field flags an "
        "empty diff (bad revspec or over-tight extension / exclusion filters).",
    ),
    "get_health": (
        "get_health(targets?, include?)",
        "Health scores + findings on three dimensions (defect / maintainability / "
        "performance). Self-check the files you touched before finishing; "
        '`include=["biomarkers"|"refactoring"|"signals"]` for depth.',
    ),
    "get_dead_code": (
        "get_dead_code()",
        "Confidence-tiered unreachable files / unused exports / zombie packages. For "
        "cleanup sweeps, not targeted fixes.",
    ),
    "get_overview": (
        "get_overview()",
        "Architecture map + tool recipes. Call once, first, in an unfamiliar repo; "
        "skip it after that.",
    ),
}


def render_tool_table() -> str:
    """Markdown table of the tool rows, in the dict's curated order."""
    lines = ["| Tool | When and why |", "|------|--------------|"]
    for signature, row in TOOL_TABLE_ROWS.values():
        lines.append(f"| `{signature}` | {row} |")
    return "\n".join(lines)
