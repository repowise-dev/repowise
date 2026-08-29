"""Whole-response budget enforcement — the shared ceilings.

Two strategies, for two payload shapes:

* :func:`truncate_to_budget` — the staged truncator ported from
  ``tool_context/truncation.py`` (which now re-exports from here). Every stage
  walks ``result["targets"][name]["docs"|"skeleton"|"symbols"]``, so it is
  ``get_context``-shaped and has one caller by design. Keep/drop decisions are
  byte-identical to the original; the additions are an optional
  :class:`OmissionCollector` and a skeleton-stripping stage.
* :func:`fit_to_budget` — sheds whole named blocks in a tool-declared order,
  for the tools whose payload is a bag of independent blocks.

``get_health`` keeps a third strategy (trims the longest ranked list by rows).

The MCP host caps the size of a tool result. An over-cap result is **spilled to
a sidecar file** the agent must Read back, not rejected: it comes back worded as
an error but carries no ``isError``. The cap is counted in tokens; the spill
message reports characters.

Observed bounds: the largest MCP result that did NOT spill was 47,276 chars,
the smallest that DID was 60,718 — consistent with a 25000-token cap at the
~2.0-2.4 chars/token dense JSON really costs. ``CHAR_BUDGET`` (32,000) is about
half that line. The residual risk is a user who *lowers*
``MAX_MCP_OUTPUT_TOKENS``, which :func:`effective_char_budget` clamps for.

The estimator is intentionally dependency-free: 4 chars/token is the
widely-quoted average for English + code on BPE tokenizers, and it undercounts
the compact JSON we emit by roughly 1.7x. ``HOST_CAP_BUDGET_FRACTION`` absorbs
that gap plus the JSON envelope and ``_meta`` the host counts on top.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from typing import Any

from repowise.server.mcp_server._budget.collector import OmissionCollector

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 8000
CHARS_PER_TOKEN = 4
CHAR_BUDGET = TOKEN_BUDGET * CHARS_PER_TOKEN

# Claude Code's MAX_MCP_OUTPUT_TOKENS default. A result over this is spilled to
# a sidecar file the agent must Read back, so our ceiling stays under it.
HOST_MCP_TOKEN_CAP_DEFAULT = 25000

# Fraction of the host cap we allow ourselves. The gap absorbs (a) estimator
# error — 4 chars/token undercounts compact JSON by roughly 1.7x — and (b) the
# JSON envelope + _meta the host tokenizes on top of our payload. 0.6 keeps even
# an undercounted response clear of the spill line.
HOST_CAP_BUDGET_FRACTION = 0.6


def host_token_cap() -> int:
    """The MCP host's max-output-tokens: ``MAX_MCP_OUTPUT_TOKENS`` or the default.

    Read at call time (not import) so a mid-session env change is honoured and
    tests can monkeypatch it. A malformed or non-positive value falls back to
    the measured default rather than trusting a footgun.
    """
    raw = os.environ.get("MAX_MCP_OUTPUT_TOKENS", "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return HOST_MCP_TOKEN_CAP_DEFAULT


def effective_char_budget(configured: int = CHAR_BUDGET) -> int:
    """``configured`` ceiling, lowered under the live host cap when that is tighter.

    Default host cap (25000) leaves our 8000-token budget untouched; a narrowed
    ``MAX_MCP_OUTPUT_TOKENS`` pulls us down with it so a response can never
    reach the host's spill-to-file path and cost the agent a Read.
    """
    host_char_ceiling = int(host_token_cap() * HOST_CAP_BUDGET_FRACTION) * CHARS_PER_TOKEN
    return min(configured, host_char_ceiling)


def estimate_response_tokens(obj: Any) -> int:
    """Cheap upper-bound token estimate for an arbitrary JSON-serialisable object.

    Serialises to compact JSON (the wire format the MCP layer eventually emits)
    and divides by ``CHARS_PER_TOKEN``. We use the serialised form — not just
    raw text fields — because structural JSON overhead (quotes, braces, field
    names) is non-trivial and is what the downstream tokenizer actually sees.
    """
    return len(json.dumps(obj, separators=(",", ":"), default=str)) // CHARS_PER_TOKEN


# Reserved for what the collector appends after the last fit check: the
# omission marker and ``_meta.omitted``.
FIT_HEADROOM_CHARS = 400


def response_chars(response: Any) -> int:
    """Serialised size of *response* in the compact JSON the MCP layer emits."""
    return len(json.dumps(response, separators=(",", ":"), default=str))


def over_budget(
    response: Any,
    *,
    headroom: int = FIT_HEADROOM_CHARS,
    char_budget: int | None = None,
) -> bool:
    """True when *response* would exceed the transport ceiling once markers land."""
    budget = effective_char_budget() if char_budget is None else char_budget
    return response_chars(response) > budget - headroom


def fit_to_budget(
    response: dict[str, Any],
    order: Sequence[str],
    collector: OmissionCollector,
    *,
    headroom: int = FIT_HEADROOM_CHARS,
    char_budget: int | None = None,
    record_counts: bool = False,
) -> dict[str, Any]:
    """Shed whole blocks named by *order* until *response* fits the budget.

    *order* is the tool's cheapest-loss-first ranking of the blocks it can live
    without. ``"parent.child"`` sheds a nested block; ``"key[]"`` drops rows
    from the tail of a ranked list instead of the list itself, keeping the
    first. Shedding stops the moment the response fits, so an under-budget
    response — the common case — is untouched.

    Drops go to *collector* as expandable ``[repowise#<ref>]`` markers and set
    ``truncated``. Call before the caller's :meth:`OmissionCollector.attach`,
    which is what ``headroom`` reserves for.
    """
    for key in order:
        if not over_budget(response, headroom=headroom, char_budget=char_budget):
            break
        container, _, leaf = key.rpartition(".")
        target: Any = response
        for part in container.split(".") if container else ():
            target = target.get(part) if isinstance(target, dict) else None
        if not isinstance(target, dict):
            continue
        if leaf.endswith("[]"):
            _shed_tail(
                response,
                target,
                leaf[:-2],
                key[:-2],
                collector,
                headroom,
                char_budget,
                record_counts,
            )
        elif target.get(leaf):
            value = target.pop(leaf)
            collector.add(key, value)
            if record_counts:
                _record_reduction(response, target, key, leaf, value, emitted=0)
            response["truncated"] = True
    return response


def _shed_tail(
    response: dict[str, Any],
    container: dict[str, Any],
    leaf: str,
    label: str,
    collector: OmissionCollector,
    headroom: int,
    char_budget: int | None,
    record_counts: bool,
) -> None:
    """Drop ranked rows from the tail of ``container[leaf]`` until it fits."""
    rows = container.get(leaf)
    if not isinstance(rows, (list, dict)):
        return
    total = len(rows)
    dropped: list[Any] = []
    while len(rows) > 1 and over_budget(
        response, headroom=headroom, char_budget=char_budget
    ):
        if isinstance(rows, list):
            dropped.append(rows.pop())
        else:
            name = next(reversed(rows))
            dropped.append({name: rows.pop(name)})
    if dropped:
        collector.add(label, list(reversed(dropped)))
        if record_counts:
            prior_reason = container.get(f"{leaf}_reduced_reason")
            collection_total = max(
                total, int(container.get(f"{leaf}_total") or 0)
            )
            container[f"{leaf}_total"] = collection_total
            container[f"{leaf}_emitted"] = len(rows)
            container[f"{leaf}_reduced_reason"] = _with_budget_reason(prior_reason)
            container[f"{leaf}_truncated"] = True
            # Construction and delivery collectors both advertise their refs
            # on the final response, so omitted is the complete recoverable
            # population difference across both passes.
            container[f"{leaf}_omitted"] = collection_total - len(rows)
        response["truncated"] = True


def _record_reduction(
    response: dict[str, Any],
    container: dict[str, Any],
    path: str,
    field: str,
    value: Any,
    *,
    emitted: int,
) -> None:
    """Keep honest counts for a collection removed as one budget block."""
    if isinstance(value, list):
        prior_reason = container.get(f"{field}_reduced_reason")
        total = max(len(value), int(container.get(f"{field}_total") or 0))
        container[f"{field}_total"] = total
        container[f"{field}_emitted"] = emitted
        container[f"{field}_reduced_reason"] = _with_budget_reason(prior_reason)
        container[f"{field}_truncated"] = True
        container[f"{field}_omitted"] = total - emitted
        return
    if not isinstance(value, dict):
        return

    reductions = response.setdefault("_meta", {}).setdefault("reductions", [])

    def visit(node: Any, node_path: str) -> None:
        if isinstance(node, list):
            reductions.append(
                {
                    "field": node_path,
                    "total": len(node),
                    "emitted": 0,
                    "reason": "response_budget",
                }
            )
        elif isinstance(node, dict):
            for name, child in node.items():
                visit(child, f"{node_path}.{name}")

    visit(value, path)


def _with_budget_reason(prior_reason: Any) -> str:
    """Append final-delivery budgeting to reduction provenance at most once."""
    if not prior_reason:
        return "response_budget"
    reason = str(prior_reason)
    if reason == "response_budget" or reason.endswith("_and_response_budget"):
        return reason
    return f"{reason}_and_response_budget"


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
    char_budget: int | None = None,
    *,
    collector: OmissionCollector | None = None,
    record_counts: bool = False,
) -> dict[str, Any]:
    """Cap a targets-shaped response at roughly ``TOKEN_BUDGET`` tokens.

    ``char_budget`` defaults to :func:`effective_char_budget` — our configured
    ceiling, clamped under the live MCP host cap so the response can never trip
    the host's spill-to-file path. Pass an explicit value to override
    (tests do; production callers should not).

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
    (see :class:`OmissionCollector`). ``record_counts`` adds the shared
    ``*_total`` / emitted / reason fields for final-delivery accounting. With
    neither option, behaviour is byte-identical to the original silent-drop
    implementation.

    Edge cases:
      * Empty ``targets`` → returns unchanged with ``truncated=False``.
      * A single target whose symbol list alone busts the budget → we reduce
        symbols down to 1 and accept the overshoot rather than returning an
        empty response. The ``truncated`` flag still fires.
      * Targets that carry an ``error`` field (not-found) are cheap and are
        preserved unless literally nothing else fits.
    """
    if char_budget is None:
        char_budget = effective_char_budget()
    try:
        result = _run_stages(result, char_budget, collector, record_counts)
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
    else:
        # Nothing was dropped, so say nothing. ``truncated: false`` +
        # ``dropped_targets: []`` + ``dropped_symbols: {}`` is 60 characters of
        # "nothing happened" on every untruncated response — and every response
        # is untruncated in the common case. Absent reads the same as empty to
        # a ``.get()``, which is how both projections already test them.
        for key in ("truncated", "dropped_targets", "dropped_symbols"):
            if not result.get(key):
                result.pop(key, None)
    return result


def _run_stages(
    result: dict[str, Any],
    char_budget: int,
    collector: OmissionCollector | None,
    record_counts: bool,
) -> dict[str, Any]:
    result.setdefault("truncated", False)
    result.setdefault("dropped_targets", [])
    result.setdefault("dropped_symbols", {})

    targets: dict[str, Any] = result.get("targets") or {}
    targets_total = len(targets)
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
        symbol_content_reduced = False
        if not kept and ordered:
            # Edge case: a single symbol is larger than the budget. Keep one
            # (truncating its docstring) rather than returning zero symbols —
            # the caller at least learns the target resolved.
            head = dict(ordered[0])
            if isinstance(head.get("docstring"), str):
                symbol_content_reduced = len(head["docstring"]) > 200
                head["docstring"] = head["docstring"][:200]
            kept = [head]
            dropped = [s.get("name") or "<anonymous>" for s in ordered[1:]]
            # The kept head lost its docstring tail too — capture the full
            # original alongside the genuinely dropped tail.
            dropped_syms = list(ordered)
        docs["symbols"] = kept
        if dropped or symbol_content_reduced:
            if record_counts:
                docs["symbols_total"] = max(
                    len(ordered), int(docs.get("symbols_total") or 0)
                )
                docs["symbols_emitted"] = len(kept)
                docs["symbols_reduced_reason"] = "response_budget"
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
        if record_counts:
            result["targets_total"] = max(
                targets_total, int(result.get("targets_total") or 0)
            )
            result["targets_emitted"] = len(targets)
            result["targets_reduced_reason"] = "response_budget"
        if _size() <= char_budget:
            break

    return result
