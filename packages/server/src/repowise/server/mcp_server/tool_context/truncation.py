"""Output-size budgeting for get_context.

The Claude Code harness rejects MCP tool results whose stringified form exceeds
~10k tokens, so get_context caps its response well below that. This module owns
the budget constants and the truncation strategy.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# --- Output size budget -------------------------------------------------------
# The Claude Code harness rejects MCP tool results whose stringified form exceeds
# ~10k tokens (it refuses to inline them and then refuses to Read the spilled
# file). When that happens the agent falls back to multiple get_symbol calls,
# each of which re-plays the cached system prompt — a significant cost driver
# on dense files in long multi-turn agent sessions.
#
# We therefore cap get_context output well below that ceiling. 8000 tokens
# leaves headroom for the wrapping JSON envelope and _meta fields the harness
# adds on top. The estimator is intentionally dependency-free: 4 chars/token is
# the widely-quoted average for English + code on BPE tokenizers and is within
# ~20% of tiktoken for typical wiki content. Precise counting is unnecessary
# because we only need to stay comfortably under the hard limit.
_TOKEN_BUDGET = 8000
_CHARS_PER_TOKEN = 4
_CHAR_BUDGET = _TOKEN_BUDGET * _CHARS_PER_TOKEN


def _estimate_tokens(obj: Any) -> int:
    """Cheap upper-bound token estimate for an arbitrary JSON-serialisable object.

    Serialises to compact JSON (the wire format the MCP layer eventually emits)
    and divides by ``_CHARS_PER_TOKEN``. We use the serialised form — not just
    raw text fields — because structural JSON overhead (quotes, braces, field
    names) is non-trivial and is what the downstream tokenizer actually sees.
    """
    return len(json.dumps(obj, separators=(",", ":"), default=str)) // _CHARS_PER_TOKEN


# Heavy optional fields we can strip from a target's docs block without losing
# its identity. Ordering matters: earlier entries are dropped first because they
# carry the most bytes per unit of navigational value.
_HEAVY_DOC_FIELDS: tuple[str, ...] = ("content_md", "documentation", "file_summary")


def _symbol_priority(sym: dict[str, Any], query_terms: set[str]) -> tuple[int, int, int]:
    """Return a sort key (higher = keep) for a symbol within a target.

    Priority order (language-agnostic — no Python-specific heuristics):
      1. Exact name match against any user query term.
      2. Substring / case-insensitive match against query terms.
      3. Kind rank: classes/types outrank functions/methods which outrank the
         rest. This mirrors navigational usefulness across Python, TS, Go,
         Rust, C++, etc. where a type anchors a module more than a helper fn.
      4. PageRank / centrality if present on the dict (forward-compatible —
         ``get_context`` doesn't currently populate it but ``_resolve_one_target``
         may in the future).
    """
    name = (sym.get("name") or "").lower()
    exact = 1 if name and name in query_terms else 0
    fuzzy = 1 if any(t and t in name for t in query_terms) else 0
    kind = (sym.get("kind") or "").lower()
    kind_rank = {
        "class": 3,
        "interface": 3,
        "struct": 3,
        "trait": 3,
        "type": 3,
        "enum": 3,
        "function": 2,
        "method": 2,
    }.get(kind, 1)
    centrality = int((sym.get("pagerank") or sym.get("centrality") or 0) * 1000)
    return (exact * 10 + fuzzy * 5 + kind_rank, centrality, -len(json.dumps(sym, default=str)))


def _query_terms_for(target: str) -> set[str]:
    """Derive cheap query terms from a target string for symbol prioritisation.

    ``get_context`` has no explicit query argument, so we fall back to the
    target identifier itself — the tail of a file path, or the raw symbol name.
    This is deliberately coarse: it just nudges symbol retention toward the
    thing the caller asked about.
    """
    tail = target.rsplit("/", 1)[-1].lower()
    # Strip common extension if present (language-agnostic: split once on '.').
    if "." in tail:
        tail = tail.rsplit(".", 1)[0]
    return {t for t in (tail, target.lower()) if t}


def _truncate_to_budget(
    result: dict[str, Any],
    char_budget: int = _CHAR_BUDGET,
) -> dict[str, Any]:
    """Cap the ``get_context`` response at roughly ``_TOKEN_BUDGET`` tokens.

    Strategy (applied in order, stopping as soon as the budget is met):

    1. **Strip heavy optional doc fields** (``content_md``, ``documentation``,
       ``file_summary``) from each target. These are 1–2k tokens apiece and
       duplicate information the agent can re-request via ``full_doc``.
    2. **Shrink symbol lists within each target**, keeping the highest-priority
       symbols per ``_symbol_priority``. This preserves the navigational index
       (names, signatures, line numbers) while dropping bulk docstrings.
    3. **Drop whole targets** from the tail of the list. Per spec we prefer
       keeping fewer full-fidelity targets over many stubs, so once symbols
       can't shrink further we evict entire targets rather than gutting them.

    Adds ``truncated: bool``, ``dropped_targets: list[str]``, and
    ``dropped_symbols: dict[target, list[name]]`` top-level fields — additive
    only, existing callers are unaffected.

    Edge cases:
      * Empty ``targets`` → returns unchanged with ``truncated=False``.
      * A single target whose symbol list alone busts the budget → we reduce
        symbols down to 1 and accept the overshoot rather than returning an
        empty response. The ``truncated`` flag still fires.
      * Targets that carry an ``error`` field (not-found) are cheap and are
        preserved unless literally nothing else fits.
    """
    result.setdefault("truncated", False)
    result.setdefault("dropped_targets", [])
    result.setdefault("dropped_symbols", {})

    targets: dict[str, Any] = result.get("targets") or {}
    if not targets:
        return result

    def _size() -> int:
        return len(json.dumps(result, separators=(",", ":"), default=str))

    if _size() <= char_budget:
        return result

    # Stage 1: strip heavy optional doc fields across all targets.
    for name, tgt in targets.items():
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        for field in _HEAVY_DOC_FIELDS:
            if field in docs:
                docs.pop(field, None)
                result["truncated"] = True
        if _size() <= char_budget:
            return result

    # Stage 2: prioritise symbols within each target. We iterate from the
    # largest target down so the biggest offenders shrink first.
    def _target_cost(item: tuple[str, Any]) -> int:
        return len(json.dumps(item[1], default=str))

    for tgt_name, tgt in sorted(targets.items(), key=_target_cost, reverse=True):
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        symbols = docs.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            continue
        query_terms = _query_terms_for(tgt_name)
        ordered = sorted(symbols, key=lambda s: _symbol_priority(s, query_terms), reverse=True)

        # Per-symbol greedy fit. The cost of the whole response with a symbol
        # list ``S`` is exactly:
        #     base + sum(cost(s) for s in S) + max(0, len(S) - 1)
        # where ``base`` is the response size with this target's ``symbols``
        # emptied and ``cost(s)`` is the symbol's compact-JSON length. Both are
        # context-independent under the compact separators we serialise with,
        # so we precompute each symbol's cost ONCE and track a running sum
        # instead of re-serialising the entire response per candidate symbol
        # (the old O(targets × symbols²) behaviour). The keep/drop decision is
        # byte-for-byte identical to the previous ``_size()``-per-symbol loop.
        costs = [len(json.dumps(s, separators=(",", ":"), default=str)) for s in ordered]
        docs["symbols"] = []
        base = _size()
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        sum_kept = 0
        for sym, cost in zip(ordered, costs, strict=True):
            # Tentative size if we add this symbol to the current kept set:
            # the +len(kept) term is the comma separators for kept+1 entries.
            tentative = base + sum_kept + cost + len(kept)
            if tentative <= char_budget:
                kept.append(sym)
                sum_kept += cost
            else:
                dropped.append(sym.get("name") or "<anonymous>")
        if not kept and ordered:
            # Edge case: a single symbol is larger than the budget. Keep one
            # (truncating its docstring) rather than returning zero symbols —
            # the caller at least learns the target resolved.
            head = dict(ordered[0])
            if isinstance(head.get("docstring"), str):
                head["docstring"] = head["docstring"][:200]
            kept = [head]
            dropped = [s.get("name") or "<anonymous>" for s in ordered[1:]]
        docs["symbols"] = kept
        if dropped:
            result["dropped_symbols"][tgt_name] = dropped
            result["truncated"] = True
        if _size() <= char_budget:
            return result

    # Stage 3: drop whole targets, largest first, until we fit. Prefer to keep
    # error-only targets (they're tiny and signal "not found" to the caller).
    def _evictable_order() -> list[str]:
        items = list(targets.items())
        items.sort(
            key=lambda kv: (
                0 if isinstance(kv[1], dict) and "error" in kv[1] else 1,
                len(json.dumps(kv[1], default=str)),
            ),
            reverse=True,
        )
        return [k for k, _ in items]

    for name in _evictable_order():
        if len(targets) <= 1:
            break
        targets.pop(name, None)
        result["dropped_targets"].append(name)
        result["truncated"] = True
        if _size() <= char_budget:
            break

    if result["truncated"]:
        logger.info(
            "get_context truncated to budget",
            extra={
                "char_budget": char_budget,
                "token_budget": _TOKEN_BUDGET,
                "final_chars": _size(),
                "dropped_targets": result["dropped_targets"],
                "dropped_symbol_counts": {k: len(v) for k, v in result["dropped_symbols"].items()},
            },
        )
    return result
