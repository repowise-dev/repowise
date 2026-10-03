"""Whole-response budget enforcement — the shared ceilings.

Two strategies, for two payload shapes:

* :func:`truncate_to_budget` — the staged truncator ported from
  ``tool_context/truncation.py`` (which now re-exports from here). Every stage
  walks ``result["targets"][name]``, so it is ``get_context``-shaped and has
  one caller by design. Each target gets a fair share of the budget and
  degrades inside it; a whole target is dropped only as a last resort.
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

The estimator is dependency-free: it charges each content class of the
serialised payload at a rate measured against the o200k_base tokenizer. A
single divisor over the whole response undercounts, worst on the payloads that
are mostly paths and identifiers. ``HOST_CAP_BUDGET_FRACTION`` absorbs the JSON
envelope and ``_meta`` the host counts on top of ours.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from repowise.server.mcp_server._budget.collector import OmissionCollector

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 8000
#: The char ceiling the truncation path measures against, not an estimate.
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


# Measured chars/token against the o200k_base tokenizer on recorded tool
# responses. Paths and identifiers run densest, prose loosest.
CHARS_PER_TOKEN_JSON_STRUCTURE = 3.7
CHARS_PER_TOKEN_PATH = 3.4
CHARS_PER_TOKEN_CODE_BODY = 3.8
CHARS_PER_TOKEN_PROSE = 4.5

_CODE_PUNCTUATION = frozenset("(){}[]<>=;:+-*/%&|!@#$^~`_.")

#: Punctuation share above which a whitespace-bearing string reads as code.
#: Calibrated so a source body lands on the code side and an English sentence
#: carrying identifiers does not.
_CODE_PUNCTUATION_SHARE = 0.05


def _content_class(text: str) -> str:
    """Classify one JSON string leaf by how densely it tokenizes."""
    if not any(character.isspace() for character in text):
        return "path"
    punctuation = sum(1 for character in text if character in _CODE_PUNCTUATION)
    return "code" if punctuation >= len(text) * _CODE_PUNCTUATION_SHARE else "prose"


def _add_leaf_chars(obj: Any, totals: dict[str, int]) -> None:
    """Accumulate the serialised length of every string leaf, per content class."""
    if isinstance(obj, str):
        totals[_content_class(obj)] += len(json.dumps(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            _add_leaf_chars(value, totals)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _add_leaf_chars(value, totals)


def estimate_response_tokens(obj: Any) -> int:
    """Token estimate for an arbitrary JSON-serialisable object.

    Measures the compact JSON the MCP layer emits rather than the text fields
    alone, because field names, quotes, and braces are what the downstream
    tokenizer sees. Each string leaf is charged at its content class's rate and
    everything left over (keys, punctuation, numbers, literals) at the
    structural one.
    """
    leaves = {"path": 0, "code": 0, "prose": 0}
    _add_leaf_chars(obj, leaves)
    structure = max(0, response_chars(obj) - sum(leaves.values()))
    return int(
        structure / CHARS_PER_TOKEN_JSON_STRUCTURE
        + leaves["path"] / CHARS_PER_TOKEN_PATH
        + leaves["code"] / CHARS_PER_TOKEN_CODE_BODY
        + leaves["prose"] / CHARS_PER_TOKEN_PROSE
    )


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


#: A requested collection keeps whichever of these is larger.
REQUESTED_MIN_ROWS = 3
REQUESTED_MIN_SHARE = 0.25


def entitled_floor(total: int) -> int:
    """Rows a caller-requested collection keeps before anything else sheds."""
    if total <= REQUESTED_MIN_ROWS:
        return total
    return min(total, max(REQUESTED_MIN_ROWS, math.ceil(total * REQUESTED_MIN_SHARE)))


def shed_stem(key: str) -> str:
    """The response path a shed-order key names, with the ``[]`` form removed."""
    return key[:-2] if key.endswith("[]") else key


def _rows_to_keep(rows: Any, requested: bool) -> int:
    """Tail-shed floor for one collection: its entitlement, or one row."""
    if not requested or not isinstance(rows, (list, dict)):
        return 1
    return entitled_floor(len(rows))


@dataclass(frozen=True)
class _ShedLimits:
    """The knobs every key in one :func:`fit_to_budget` pass shares."""

    headroom: int
    char_budget: int | None
    record_counts: bool

    def exceeded(self, response: dict[str, Any]) -> bool:
        return over_budget(
            response, headroom=self.headroom, char_budget=self.char_budget
        )


def fit_to_budget(
    response: dict[str, Any],
    order: Sequence[str],
    collector: OmissionCollector,
    *,
    headroom: int = FIT_HEADROOM_CHARS,
    char_budget: int | None = None,
    record_counts: bool = False,
    entitled: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Shed whole blocks named by *order* until *response* fits the budget.

    *order* is the tool's cheapest-loss-first ranking of the blocks it can live
    without. ``"parent.child"`` sheds a nested block; ``"key[]"`` drops rows
    from the tail of a ranked list instead of the list itself, keeping the
    first. Shedding stops the moment the response fits, so an under-budget
    response — the common case — is untouched.

    *entitled* names stems the caller asked for; their ``[]`` passes stop at
    :func:`entitled_floor` instead of one row. The order still decides when a
    block is reached, and the ceiling still wins.

    Drops go to *collector* as expandable ``[repowise#<ref>]`` markers and set
    ``truncated``. Call before the caller's :meth:`OmissionCollector.attach`,
    which is what ``headroom`` reserves for.
    """
    limits = _ShedLimits(headroom, char_budget, record_counts)
    requested = entitled or frozenset()
    for key in order:
        if not limits.exceeded(response):
            break
        container, _, leaf = key.rpartition(".")
        target: Any = response
        for part in container.split(".") if container else ():
            target = target.get(part) if isinstance(target, dict) else None
        if not isinstance(target, dict):
            continue
        if leaf.endswith("[]"):
            keep = _rows_to_keep(target.get(leaf[:-2]), shed_stem(key) in requested)
            _shed_tail(response, target, leaf[:-2], key[:-2], collector, limits, keep)
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
    limits: _ShedLimits,
    keep: int = 1,
) -> None:
    """Drop ranked rows from the tail of ``container[leaf]`` down to *keep*."""
    rows = container.get(leaf)
    if not isinstance(rows, (list, dict)):
        return
    total = len(rows)
    dropped: list[Any] = []
    while len(rows) > keep and limits.exceeded(response):
        if isinstance(rows, list):
            dropped.append(rows.pop())
        else:
            name = next(reversed(rows))
            dropped.append({name: rows.pop(name)})
    if dropped:
        collector.add(label, list(reversed(dropped)))
        if limits.record_counts:
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

    def visit(node: Any, node_path: str, parent: dict[str, Any], name: str) -> None:
        if isinstance(node, list):
            # An earlier tail-shed may already have trimmed this list and left
            # the population beside it. Reporting len() here would count only
            # what the trim left, not what the caller lost overall.
            reductions.append(
                {
                    "field": node_path,
                    "total": max(len(node), int(parent.get(f"{name}_total") or 0)),
                    "emitted": 0,
                    "reason": "response_budget",
                }
            )
        elif isinstance(node, dict):
            for child_name, child in node.items():
                visit(child, f"{node_path}.{child_name}", node, child_name)

    visit(value, path, container, field)


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
HEAVY_DOC_FIELDS: tuple[str, ...] = (
    "content_md",
    "digest_md",
    "documentation",
    "file_summary",
)


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


#: Keys a degraded target always keeps: what it is, whether it resolved, and
#: the did-you-mean list or ambiguity candidates that answer a miss.
_TARGET_IDENTITY_KEYS = frozenset(
    {
        "target",
        "type",
        "path",
        "error",
        "suggestions",
        "docs",
        "parent_page",
        "freshness",
        "fix_history",
    }
)
_DOCS_IDENTITY_KEYS = frozenset({"title", "summary", "symbols", "candidates"})


def _identity_card(tgt: dict[str, Any]) -> dict[str, Any]:
    """What :func:`_fit_target` can reduce *tgt* to at most: identity and one symbol."""
    card = {
        key: value
        for key, value in tgt.items()
        if key in _TARGET_IDENTITY_KEYS or not isinstance(value, (list, dict))
    }
    docs = tgt.get("docs")
    if isinstance(docs, dict):
        card["docs"] = {
            key: value[:1] if key == "symbols" and isinstance(value, list) else value
            for key, value in docs.items()
            if key in _DOCS_IDENTITY_KEYS or not isinstance(value, (list, dict))
        }
    return card


def _symbols_trimmed(tgt: dict[str, Any]) -> dict[str, Any]:
    """*tgt* with its symbols cut to their entitled floor, for sizing only."""
    docs = tgt.get("docs")
    symbols = docs.get("symbols") if isinstance(docs, dict) else None
    if not isinstance(symbols, list):
        return tgt
    floor = entitled_floor(max(len(symbols), int(docs.get("symbols_total") or 0)))
    return {**tgt, "docs": {**docs, "symbols": symbols[:floor]}}


def _fit_target(
    result: dict[str, Any],
    name: str,
    tgt: dict[str, Any],
    share: int,
    collector: OmissionCollector | None,
    record_counts: bool,
) -> None:
    """Degrade one target toward *share* chars.

    Symbols trim to their entitled floor, then optional blocks go largest
    first, then symbols trim to one. A target that still exceeds its share
    after that is only its identity card, and stage 3 decides.
    """
    docs = tgt.get("docs") if isinstance(tgt.get("docs"), dict) else None
    symbols = docs.get("symbols") if docs is not None else None
    if isinstance(symbols, list) and symbols:
        query_terms = query_terms_for(name)
        ordered = sorted(symbols, key=lambda s: symbol_priority(s, query_terms), reverse=True)
        # The floor is the whole list's, so a later pass cannot erode it.
        floor = entitled_floor(max(len(ordered), int(docs.get("symbols_total") or 0)))
        _trim_symbols(result, name, tgt, ordered, share, floor, collector, record_counts)

    def cost() -> int:
        return len(json.dumps(tgt, separators=(",", ":"), default=str))

    # (container, key, label): the target's own blocks and those nested in docs.
    sources = ((tgt, _TARGET_IDENTITY_KEYS, ""), (docs or {}, _DOCS_IDENTITY_KEYS, "docs."))
    blocks = [
        (container, key, prefix + key)
        for container, keep, prefix in sources
        for key, value in container.items()
        if key not in keep and isinstance(value, (list, dict))
    ]
    blocks.sort(key=lambda block: len(json.dumps(block[0][block[1]], default=str)), reverse=True)
    for container, key, label in blocks:
        if cost() <= share:
            return
        value = container.pop(key)
        if collector is not None and value:
            collector.add(f"{name} :: {label}", value)
        result.setdefault("dropped_blocks", {}).setdefault(name, []).append(label)
        result["truncated"] = True
        if record_counts and isinstance(value, list):
            # Keeps a capped list's *_total / *_emitted beside it truthful.
            _record_reduction(result, container, f"targets.{name}.{label}", key, value, emitted=0)

    if cost() > share and isinstance(symbols, list) and symbols:
        _trim_symbols(result, name, tgt, list(docs["symbols"]), share, 1, collector, record_counts)
        head = docs["symbols"][0]
        docstring = head.get("docstring")
        if cost() > share and isinstance(docstring, str) and len(docstring) > 200:
            # One symbol alone over the share: keep it, cut its docstring.
            if collector is not None:
                collector.add(f"{name} :: {head.get('name')} docstring", docstring)
            docs["symbols"][0] = {**head, "docstring": docstring[:200]}
            result["truncated"] = True


def _trim_symbols(
    result: dict[str, Any],
    name: str,
    tgt: dict[str, Any],
    ordered: list[dict[str, Any]],
    share: int,
    floor: int,
    collector: OmissionCollector | None,
    record_counts: bool,
) -> None:
    """Keep the highest-priority symbols that fit *share*, never fewer than *floor*.

    Compact JSON is additive, so the target's size with a symbol list ``S`` is
    its size with no symbols plus each kept symbol's cost plus the commas.
    """
    docs = tgt["docs"]
    costs = [len(json.dumps(s, separators=(",", ":"), default=str)) for s in ordered]
    docs["symbols"] = []
    base = len(json.dumps(tgt, separators=(",", ":"), default=str))
    kept: list[dict[str, Any]] = []
    used = 0
    for sym, sym_cost in zip(ordered, costs, strict=True):
        if len(kept) < floor or base + used + sym_cost + len(kept) <= share:
            kept.append(sym)
            used += sym_cost
    docs["symbols"] = kept
    if len(kept) == len(ordered):
        return
    kept_ids = {id(sym) for sym in kept}
    dropped = [sym for sym in ordered if id(sym) not in kept_ids]
    result["dropped_symbols"].setdefault(name, []).extend(
        sym.get("name") or "<anonymous>" for sym in dropped
    )
    result["truncated"] = True
    if record_counts:
        docs["symbols_total"] = max(len(ordered), int(docs.get("symbols_total") or 0))
        docs["symbols_emitted"] = len(kept)
        docs["symbols_reduced_reason"] = "response_budget"
    if collector is not None:
        collector.add(
            f"{name} :: symbols dropped from response",
            "\n".join(json.dumps(s, separators=(",", ":"), default=str) for s in dropped),
        )


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
    2.   **Fair share per target.** Each target gets an equal share of the
         room the envelope leaves, with a small target's slack passed on. A
         target over its share degrades in place (see :func:`_fit_target`):
         symbols to their entitled floor, optional blocks largest first, then
         symbols to one. Every target the caller named stays visible.
         When the identity cards alone cannot fit (many targets on a narrow
         budget), whole targets are evicted first, so the survivors keep
         their detail.
    3.   **Drop whole targets**, largest first, as the backstop.

    Adds ``truncated: bool``, ``dropped_targets: list[str]``,
    ``dropped_symbols: dict[target, list[name]]`` and
    ``dropped_blocks: dict[target, list[key]]`` top-level fields.

    With a *collector*, every dropped piece of content is also captured and
    persisted, and the response gains ``omission_marker`` + ``_meta.omitted``
    (see :class:`OmissionCollector`). ``record_counts`` adds the shared
    ``*_total`` / emitted / reason fields for final-delivery accounting. With
    neither option, drops are silent.

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
                "dropped_blocks": result.get("dropped_blocks", {}),
            },
        )
    else:
        # Nothing was dropped, so say nothing. ``truncated: false`` +
        # ``dropped_targets: []`` + ``dropped_symbols: {}`` is 60 characters of
        # "nothing happened" on every untruncated response — and every response
        # is untruncated in the common case. Absent reads the same as empty to
        # a ``.get()``, which is how both projections already test them.
        for key in ("truncated", "dropped_targets", "dropped_symbols", "dropped_blocks"):
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

    def _cost(value: Any) -> int:
        return len(json.dumps(value, separators=(",", ":"), default=str))

    # Largest first; error cards last, since they are small and say "not found".
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

    def _evict(name: str) -> None:
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

    # Stage 2a: when even the identity cards cannot all fit, some targets must
    # go anyway. Evict until the rest fit with only their symbols trimmed, so
    # the survivors keep their blocks instead of every target being gutted.
    def _size_with(card: Any) -> int:
        envelope = _size() - sum(_cost(t) for t in targets.values())
        return envelope + sum(
            _cost(card(t)) if isinstance(t, dict) else _cost(t) for t in targets.values()
        )

    if _size_with(_identity_card) > char_budget:
        for name in _evictable_order():
            if len(targets) <= 1 or _size_with(_symbols_trimmed) <= char_budget:
                break
            _evict(name)
        if _size() <= char_budget:
            return result

    # Stage 2b: every target gets a fair share of what the envelope leaves.
    # Water-filling, smallest first: a target under its share keeps everything
    # and its slack passes to the rest; one over it degrades in place. Each
    # pass sees the dropped_* bookkeeping the previous one added to the
    # envelope; stage 3 stays the backstop if that still does not settle.
    for _ in range(3):
        costs = {name: _cost(tgt) for name, tgt in targets.items()}
        room = char_budget - (_size() - sum(costs.values()))
        by_cost = sorted(targets, key=lambda name: costs[name])
        for index, tgt_name in enumerate(by_cost):
            share = room // (len(by_cost) - index)
            tgt = targets[tgt_name]
            if costs[tgt_name] > share and isinstance(tgt, dict):
                _fit_target(result, tgt_name, tgt, share, collector, record_counts)
            room -= _cost(tgt)
        if _size() <= char_budget:
            return result

    # Stage 3: drop whole targets, largest first, until we fit.
    for name in _evictable_order():
        if len(targets) <= 1:
            break
        _evict(name)
        if _size() <= char_budget:
            break

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
