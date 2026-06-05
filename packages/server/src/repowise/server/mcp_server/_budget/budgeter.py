"""Staged whole-response truncation — the shared budgeter.

Ported from ``tool_context/truncation.py`` (which now re-exports from here)
so every tool shares one budget strategy instead of ad-hoc caps. The
keep/drop decisions are byte-identical to the original implementation; the
only additions are (a) an optional :class:`OmissionCollector` that makes
every drop recoverable, and (b) a skeleton-stripping stage for the
``include=["skeleton"]`` blocks that did not exist when the original was
written.

The Claude Code harness rejects MCP tool results whose stringified form
exceeds ~10k tokens (it refuses to inline them and then refuses to Read the
spilled file). When that happens the agent falls back to multiple get_symbol
calls, each of which re-plays the cached system prompt — a significant cost
driver on dense files in long multi-turn agent sessions. We therefore cap
responses well below that ceiling: 8000 tokens leaves headroom for the
wrapping JSON envelope and the ``_meta`` fields the harness adds on top.

The estimator is intentionally dependency-free: 4 chars/token is the
widely-quoted average for English + code on BPE tokenizers and is within
~20% of tiktoken for typical wiki content. Precise counting is unnecessary
because we only need to stay comfortably under the hard limit.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from repowise.server.mcp_server._budget.collector import OmissionCollector

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 8000
CHARS_PER_TOKEN = 4
CHAR_BUDGET = TOKEN_BUDGET * CHARS_PER_TOKEN


def estimate_response_tokens(obj: Any) -> int:
    """Cheap upper-bound token estimate for an arbitrary JSON-serialisable object.

    Serialises to compact JSON (the wire format the MCP layer eventually emits)
    and divides by ``CHARS_PER_TOKEN``. We use the serialised form — not just
    raw text fields — because structural JSON overhead (quotes, braces, field
    names) is non-trivial and is what the downstream tokenizer actually sees.
    """
    return len(json.dumps(obj, separators=(",", ":"), default=str)) // CHARS_PER_TOKEN


# Heavy optional fields we can strip from a target's docs block without losing
# its identity. Ordering matters: earlier entries are dropped first because they
# carry the most bytes per unit of navigational value.
HEAVY_DOC_FIELDS: tuple[str, ...] = ("content_md", "documentation", "file_summary")


def symbol_priority(sym: dict[str, Any], query_terms: set[str]) -> tuple[int, int, int]:
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


def query_terms_for(target: str) -> set[str]:
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


def truncate_to_budget(
    result: dict[str, Any],
    char_budget: int = CHAR_BUDGET,
    *,
    collector: OmissionCollector | None = None,
) -> dict[str, Any]:
    """Cap a targets-shaped response at roughly ``TOKEN_BUDGET`` tokens.

    Strategy (applied in order, stopping as soon as the budget is met):

    1.   **Strip heavy optional doc fields** (``content_md``, ``documentation``,
         ``file_summary``) from each target. These are 1-2k tokens apiece and
         duplicate information the agent can re-request via ``full_doc``.
    1.5. **Strip skeleton texts**, largest first. A skeleton block can be ~2k
         tokens per target; its text is replaced in-place by an omission
         marker (when a collector is present) so it stays one call away.
    2.   **Shrink symbol lists within each target**, keeping the highest-priority
         symbols per ``symbol_priority``. This preserves the navigational index
         (names, signatures, line numbers) while dropping bulk docstrings.
    3.   **Drop whole targets** from the tail of the list. Per spec we prefer
         keeping fewer full-fidelity targets over many stubs, so once symbols
         can't shrink further we evict entire targets rather than gutting them.

    Adds ``truncated: bool``, ``dropped_targets: list[str]``, and
    ``dropped_symbols: dict[target, list[name]]`` top-level fields — additive
    only, existing callers are unaffected.

    With a *collector*, every dropped piece of content is also captured and
    persisted, and the response gains ``omission_marker`` + ``_meta.omitted``
    (see :class:`OmissionCollector`). Without one, behaviour is byte-identical
    to the original silent-drop implementation.

    Edge cases:
      * Empty ``targets`` → returns unchanged with ``truncated=False``.
      * A single target whose symbol list alone busts the budget → we reduce
        symbols down to 1 and accept the overshoot rather than returning an
        empty response. The ``truncated`` flag still fires.
      * Targets that carry an ``error`` field (not-found) are cheap and are
        preserved unless literally nothing else fits.
    """
    try:
        result = _run_stages(result, char_budget, collector)
    finally:
        if collector is not None:
            collector.attach(result)

    if result.get("truncated"):
        logger.info(
            "response truncated to budget",
            extra={
                "char_budget": char_budget,
                "token_budget": TOKEN_BUDGET,
                "final_chars": len(json.dumps(result, separators=(",", ":"), default=str)),
                "dropped_targets": result["dropped_targets"],
                "dropped_symbol_counts": {k: len(v) for k, v in result["dropped_symbols"].items()},
            },
        )
    return result


def _run_stages(
    result: dict[str, Any],
    char_budget: int,
    collector: OmissionCollector | None,
) -> dict[str, Any]:
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
        for field in HEAVY_DOC_FIELDS:
            if field in docs:
                value = docs.pop(field, None)
                if collector is not None and value:
                    collector.add(f"{name} :: {field}", value)
                result["truncated"] = True
        if _size() <= char_budget:
            return result

    # Stage 1.5: strip skeleton texts, largest first. The skeleton block's
    # metadata (token counts, bodies_kept) survives; only the bulky text is
    # swapped for its marker so the agent knows exactly what it lost and how
    # to get it back without re-running the whole call.
    def _skeleton_cost(item: tuple[str, Any]) -> int:
        tgt = item[1]
        skel = tgt.get("skeleton") if isinstance(tgt, dict) else None
        text = skel.get("text") if isinstance(skel, dict) else None
        return len(text) if isinstance(text, str) else 0

    for tgt_name, tgt in sorted(targets.items(), key=_skeleton_cost, reverse=True):
        skel = tgt.get("skeleton") if isinstance(tgt, dict) else None
        if not isinstance(skel, dict):
            continue
        text = skel.get("text")
        if not isinstance(text, str) or not text:
            continue
        marker = collector.add_inline(f"skeleton of {tgt_name}", text) if collector else None
        if marker:
            skel["text"] = marker
        else:
            skel.pop("text", None)
            skel["note"] = (
                "Skeleton text dropped to fit the response budget; re-request with fewer targets."
            )
        skel["omitted"] = True
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
        query_terms = query_terms_for(tgt_name)
        ordered = sorted(symbols, key=lambda s: symbol_priority(s, query_terms), reverse=True)

        # Per-symbol greedy fit. The cost of the whole response with a symbol
        # list ``S`` is exactly:
        #     base + sum(cost(s) for s in S) + max(0, len(S) - 1)
        # where ``base`` is the response size with this target's ``symbols``
        # emptied and ``cost(s)`` is the symbol's compact-JSON length. Both are
        # context-independent under the compact separators we serialise with,
        # so we precompute each symbol's cost ONCE and track a running sum
        # instead of re-serialising the entire response per candidate symbol
        # (the old O(targets x symbols^2) behaviour). The keep/drop decision is
        # byte-for-byte identical to the previous ``_size()``-per-symbol loop.
        costs = [len(json.dumps(s, separators=(",", ":"), default=str)) for s in ordered]
        docs["symbols"] = []
        base = _size()
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        dropped_syms: list[dict[str, Any]] = []
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
                dropped_syms.append(sym)
        if not kept and ordered:
            # Edge case: a single symbol is larger than the budget. Keep one
            # (truncating its docstring) rather than returning zero symbols —
            # the caller at least learns the target resolved.
            head = dict(ordered[0])
            if isinstance(head.get("docstring"), str):
                head["docstring"] = head["docstring"][:200]
            kept = [head]
            dropped = [s.get("name") or "<anonymous>" for s in ordered[1:]]
            # The kept head lost its docstring tail too — capture the full
            # original alongside the genuinely dropped tail.
            dropped_syms = list(ordered)
        docs["symbols"] = kept
        if dropped:
            result["dropped_symbols"][tgt_name] = dropped
            result["truncated"] = True
            if collector is not None and dropped_syms:
                collector.add(
                    f"{tgt_name} :: symbols dropped from response",
                    "\n".join(
                        json.dumps(s, separators=(",", ":"), default=str) for s in dropped_syms
                    ),
                )
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
        evicted = targets.pop(name, None)
        if collector is not None and evicted is not None:
            collector.add(f"dropped target {name}", evicted)
        result["dropped_targets"].append(name)
        result["truncated"] = True
        if _size() <= char_budget:
            break

    return result
