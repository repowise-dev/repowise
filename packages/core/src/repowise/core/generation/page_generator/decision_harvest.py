"""Phase-2 LLM-docs decision harvest.

The page generator already runs an LLM over every Tier-1 file page with rich
graph + git context. This module makes that same call *also* surface candidate
architectural decisions at near-zero marginal cost: the system prompt is
extended with a directive instructing the model to append a single trailing
JSON block when — and only when — a real decision is evident, and this module
parses that block back out, strips it from the page markdown, and turns it into
gated ``ExtractedDecision`` candidates.

Two guardrails make this safe to enable by default:

1. **Emit-nothing-on-no-decision.** The directive tells the model to append the
   block only on a genuine hit, so output-token cost is incurred only on files
   that actually carry a decision — never on the long tail.
2. **The substring gate.** Because the model is now *generating* candidates
   rather than reading them, every harvested ``decision`` / ``rationale`` /
   ``source_quote`` is run through the same anti-hallucination gate
   (:func:`decision_gate.apply_substring_gate`) against the file's verbatim
   source. Ungrounded fields are dropped and evidence-less candidates rejected.

Scope: harvesting is restricted to ``file_page`` — it is the only Tier-1 page
type with a single, clean verbatim source span (the file's own bytes) for the
gate to verify quotes against. Module/layer pages synthesise across many files
and have no such span, so harvesting there would defeat the gate; they are
deliberately excluded.
"""

from __future__ import annotations

import dataclasses
import json
import re

import structlog

from repowise.core.analysis.decision_extractor import ExtractedDecision
from repowise.core.analysis.decision_gate import apply_substring_gate

log = structlog.get_logger(__name__)

__all__ = [
    "HARVESTABLE_PAGE_TYPES",
    "HARVEST_DIRECTIVE",
    "harvest_decisions",
    "parse_and_strip_decisions",
]

# Only page types with a single verbatim source span the gate can verify
# against. See module docstring for why module/layer pages are excluded.
HARVESTABLE_PAGE_TYPES: frozenset[str] = frozenset({"file_page"})

# Fenced-block info string used to tag the harvest payload. A dedicated tag
# (rather than a bare ```json fence) lets us locate the block unambiguously and
# avoids colliding with legitimate JSON examples inside the documentation body.
_FENCE_TAG = "repowise-decisions"

# Appended to the file_page system prompt when harvesting is enabled. Kept here
# (next to the parser) so the emit contract and the parse contract evolve
# together. The directive is intentionally strict about emitting nothing on a
# miss — that bound is the whole cost story (see 2D).
HARVEST_DIRECTIVE = (
    "\n\n"
    "DECISION HARVEST (machine-read — follow exactly):\n"
    "After the markdown documentation, decide whether this file's code, "
    "structure, and the supplied git/architectural signals reveal a real, "
    "concrete architectural DECISION (a deliberate choice with a rationale — "
    "e.g. adopting a library, a pattern, a data store, a protocol, or rejecting "
    "an alternative). Most files embody no such decision.\n"
    f"- If a genuine decision is evident, append EXACTLY ONE fenced ```{_FENCE_TAG} "
    "block containing a JSON object: "
    '{"decisions": [{"title": "...", "decision": "...", "rationale": "...", '
    '"source_quote": "..."}]}. '
    "The source_quote MUST be a verbatim substring copied from this file's "
    "source — not paraphrased — and is what proves the decision is real. "
    'Keep titles short and canonical (e.g. "Use Redis for caching").\n'
    "- If NO real decision is evident, output NOTHING after the markdown — no "
    "block, no empty block, no commentary. Inventing a decision is worse than "
    "emitting none."
)

# Match a trailing ```repowise-decisions … ``` fence anywhere in the content
# (DOTALL so the JSON body may span lines). We take the LAST match so a fence
# quoted inside the prose body can't shadow the real trailing payload.
_FENCE_RE = re.compile(
    rf"```[ \t]*{re.escape(_FENCE_TAG)}[ \t]*\r?\n(?P<body>.*?)\r?\n?```",
    re.DOTALL | re.IGNORECASE,
)


def parse_and_strip_decisions(content: str) -> tuple[str, list[dict]]:
    """Split a harvest fence off the end of *content*.

    Returns ``(clean_content, decisions)`` where ``clean_content`` is the
    markdown with the fence removed (trailing whitespace trimmed) and
    ``decisions`` is the parsed ``decisions`` list (empty when the block is
    absent or malformed — a harvest failure must never corrupt the page).
    """
    matches = list(_FENCE_RE.finditer(content))
    if not matches:
        return content, []

    block = matches[-1]
    clean = (content[: block.start()] + content[block.end() :]).rstrip()

    raw = block.group("body").strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.debug("decision_harvest.parse_failed", body_len=len(raw))
        return clean, []

    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(decisions, list):
        return clean, []

    out = [d for d in decisions if isinstance(d, dict) and (d.get("title") or "").strip()]
    return clean, out


def harvest_decisions(
    content: str,
    *,
    source_text: str,
    evidence_file: str,
    affected_modules: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Parse, strip, and gate harvested decisions for one page.

    Args:
        content:          The raw LLM page content (may carry a trailing fence).
        source_text:      The file's verbatim source — what the gate verifies
                          each harvested quote against.
        evidence_file:    The file path the decisions are attributed to.
        affected_modules: Optional module hints for the decisions.

    Returns ``(clean_content, gated_decision_dicts)``. The dicts are ready for
    ``crud.bulk_upsert_decisions`` — ``source="llm_inferred"`` (lowest rank, so
    a harvested decision never outranks a real ADR), ``status="proposed"``, and
    ``verification`` stamped by the gate. Ungrounded candidates are dropped.
    """
    clean, raw = parse_and_strip_decisions(content)
    if not raw:
        return clean, []

    candidates: list[ExtractedDecision] = []
    for d in raw:
        candidates.append(
            ExtractedDecision(
                title=str(d.get("title", "")).strip(),
                decision=str(d.get("decision", "")).strip(),
                rationale=str(d.get("rationale", "")).strip(),
                source_quote=str(d.get("source_quote", "")).strip(),
                affected_files=[evidence_file],
                affected_modules=list(affected_modules or []),
                source="llm_inferred",
                evidence_file=evidence_file,
                status="proposed",
                # The gate verifies the quote/decision/rationale against this.
                source_text=source_text,
            )
        )

    kept, rejected = apply_substring_gate(candidates)
    if rejected:
        log.info(
            "decision_harvest.gate_rejected",
            file=evidence_file,
            kept=len(kept),
            rejected=rejected,
        )

    return clean, [dataclasses.asdict(d) for d in kept]
