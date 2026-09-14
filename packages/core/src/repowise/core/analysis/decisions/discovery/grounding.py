"""Nothing reaches the store that its cited spans do not say.

The bar is the one the deterministic miner already enforces: a quote must
verify against the exact text the model was shown, and the claim itself must
overlap that text. Broad discovery reads far more prose than the gates do, so
it needs the same gate at a wider mouth, not a looser one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.decisions.discovery.spans import ProseSpan
from repowise.core.analysis.decisions.provenance import verify_quote

__all__ = [
    "GroundedCandidate",
    "GroundingResult",
    "ground_candidates",
    "parse_response",
]

_PUNCT_RE = re.compile(r"[^\w\s]")

#: Claims that say nothing about this repository. A generic maxim costs a
#: reviewer the same minute as a real decision and teaches nothing.
_GENERIC_CLAIMS = (
    "write good code",
    "follow best practices",
    "keep the code clean",
    "add tests",
    "avoid bugs",
    "be careful",
)

#: Below this a "decision" is a fragment, not a rule.
_MIN_DECISION_CHARS = 20

#: A claim joining independent choices is flagged, never split by machine: a
#: wrong split files one of them under the other's evidence.
_SPLIT_MARKERS = ("; ", " and also ", " and, ")

#: Dropped before scoring a claim against its spans. Without this a claim is
#: carried by its function words: "always deploy to production on fridays"
#: scores 2/6 on any span containing "to" and "on", which clears a 0.3 gate
#: while sharing no content with the evidence at all.
_STOPWORDS = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does", "for",
    "from", "has", "have", "if", "in", "into", "is", "it", "its", "must", "no", "not", "of",
    "on", "or", "should", "that", "the", "their", "then", "there", "these", "this", "to",
    "use", "using", "was", "we", "were", "when", "which", "while", "will", "with", "you",
    "your",
))

#: Fraction of a claim's content words that must appear in its cited spans.
_CLAIM_OVERLAP = 0.5

#: Fraction of an evidence quote's tokens that must appear in the cited spans
#: when it is not an exact substring of them.
_QUOTE_OVERLAP = 0.9


@dataclass(frozen=True, slots=True)
class GroundedCandidate:
    """One candidate that survived the gate, with its evidence resolved."""

    title: str
    decision: str
    rationale: str
    kind: str
    acceptance_basis: str
    source_quote: str
    verification: str
    spans: tuple[ProseSpan, ...]
    affected_files: tuple[str, ...]
    needs_split: bool

    @property
    def session_id(self) -> str | None:
        return self.spans[0].session_id if self.spans else None

    @property
    def quotes(self) -> list[str]:
        return [span.text for span in self.spans]

    def to_structured(self) -> dict[str, Any]:
        """The staging blob.

        The first six keys are the deterministic lane's field names on purpose:
        promotion, evidence and confidence read this dict without a second
        branch for where the candidate came from.
        """
        return {
            "title": self.title,
            "decision": self.decision,
            "rationale": self.rationale,
            "source_quote": self.source_quote,
            "verification": self.verification,
            "affected_files": list(self.affected_files),
            "kind": self.kind,
            "acceptance_basis": self.acceptance_basis,
            "needs_split": self.needs_split,
            "span_ids": [span.span_id for span in self.spans],
            "discovery": True,
        }


@dataclass(slots=True)
class GroundingResult:
    """What one response yielded, and why the rest of it did not."""

    grounded: list[GroundedCandidate] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    returned: int = 0
    rejected_task_local: int = 0
    rejected_assistant_only: int = 0

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def parse_response(content: str) -> dict[str, Any] | None:
    """Parse the model's reply, tolerating fenced or prose-wrapped JSON."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _is_generic(decision: str) -> bool:
    low = decision.lower()
    return len(decision) < _MIN_DECISION_CHARS or any(g in low for g in _GENERIC_CLAIMS)


def _claim_overlaps(decision: str, plain_source: str) -> bool:
    """Whether the claim's content words are actually present in the evidence."""
    words = {w for w in _PUNCT_RE.sub(" ", decision).lower().split() if w not in _STOPWORDS}
    if not words:
        return False
    source = set(plain_source.lower().split())
    return len(words & source) / len(words) >= _CLAIM_OVERLAP


def _as_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def ground_candidates(
    payload: dict[str, Any] | None,
    packet_spans: tuple[ProseSpan, ...],
    known_paths: tuple[str, ...] = (),
) -> GroundingResult:
    """Gate every returned candidate against the spans it claims to rest on."""
    result = GroundingResult()
    if payload is None:
        result.reject("malformed")
        return result
    result.rejected_task_local = _as_count(payload.get("rejected_task_local"))
    result.rejected_assistant_only = _as_count(payload.get("rejected_assistant_only"))

    items = payload.get("candidates")
    if not isinstance(items, list):
        result.reject("malformed")
        return result

    by_id = {span.span_id: span for span in packet_spans}
    selectable = set(known_paths) or {path for span in packet_spans for path in span.files}

    for item in items:
        result.returned += 1
        if not isinstance(item, dict):
            result.reject("malformed")
            continue
        candidate = _ground_one(item, by_id, selectable, result)
        if candidate is not None:
            result.grounded.append(candidate)
    return result


def _ground_one(
    item: dict[str, Any],
    by_id: dict[str, ProseSpan],
    known_paths: set[str],
    result: GroundingResult,
) -> GroundedCandidate | None:
    title = str(item.get("title") or "").strip()
    decision = str(item.get("decision") or "").strip()
    if not title or not decision:
        result.reject("empty_claim")
        return None
    if _is_generic(decision):
        result.reject("generic_claim")
        return None
    if str(item.get("durability") or "durable").strip().lower() != "durable":
        result.reject("task_local")
        return None

    raw_ids = item.get("span_ids")
    ids = list(dict.fromkeys(str(i) for i in raw_ids)) if isinstance(raw_ids, list) else []
    spans = tuple(by_id[i] for i in ids if i in by_id)
    # An invented span id is the tell that the rest of the object was invented
    # too, so the candidate dies rather than falling back to whichever ids did
    # resolve.
    if not spans or len(spans) != len(ids):
        result.reject("unknown_span")
        return None

    source_text = "\n".join(span.text for span in spans)
    quote = str(item.get("evidence_quote") or "").strip()
    # Near-verbatim, not the deterministic lane's 0.6. That lane's excerpts are
    # the few sentences its gates already matched, so a loose bar is scored
    # against a small target; this lane shows the model thousands of words,
    # where 60% token overlap with *some* part of them is not evidence of
    # anything. Exact-substring alone was measured rejecting 4 of 7 real
    # candidates over reflow and dropped articles, which is the wrong trade for
    # a lane that exists to recover recall, so a quote that is all-but-verbatim
    # still counts and is recorded as `fuzzy` for confidence to discount.
    verification = verify_quote(quote, source_text, fuzzy_threshold=_QUOTE_OVERLAP)
    if verification == "unverified":
        result.reject("unverified_quote")
        return None

    plain_source = _PUNCT_RE.sub(" ", source_text)
    if not _claim_overlaps(decision, plain_source):
        result.reject("ungrounded_claim")
        return None

    rationale = str(item.get("rationale") or "").strip()
    if rationale and (
        verify_quote(_PUNCT_RE.sub(" ", rationale), plain_source, fuzzy_threshold=0.5)
        == "unverified"
    ):
        rationale = ""  # never an invented why; the candidate itself still stands

    # Scope is what the model selected from the resolved known-node set, and
    # nothing else. A path it wrote down that no cited turn touched is dropped;
    # so is a fallback to whatever files happened to be open, which is how a
    # repository-wide rule ends up pinned to an unrelated file. Selecting
    # nothing is an honest repository-wide claim.
    claimed = item.get("paths")
    files = (
        [p for p in claimed if isinstance(p, str) and p in known_paths]
        if isinstance(claimed, list)
        else []
    )

    low = decision.lower()
    return GroundedCandidate(
        title=title,
        decision=decision,
        rationale=rationale,
        kind=str(item.get("kind") or "").strip().lower(),
        acceptance_basis=str(item.get("acceptance_basis") or "").strip().lower(),
        source_quote=quote,
        verification=verification,
        spans=spans,
        affected_files=tuple(dict.fromkeys(files)),
        needs_split=any(marker in low for marker in _SPLIT_MARKERS),
    )
