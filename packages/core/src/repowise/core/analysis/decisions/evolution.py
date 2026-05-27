"""Decision evolution — supersession/conflict detection + two-pass update scan.

Phase 3B + 3C of the decision-layer overhaul. Two complementary mechanisms,
both small and gated, both leaning on the Phase-2 primitives
(:data:`decision_semantic_match.DEFAULT_DEDUP_TAU` for similarity and
:func:`decision_gate.apply_substring_gate` for anti-hallucination):

**3B — semantic supersession & conflict** (:func:`detect_supersessions_and_conflicts`).
    When a freshly upserted decision is semantically *about the same topic* as
    an existing one (cosine in the band ``[RELATED_TAU, DEDUP_TAU)`` — related
    but not a duplicate) **and** contradicts it (opposing verbs / a reversal
    signal, optionally confirmed by a gated LLM judge), record a typed edge:
    ``supersedes`` when one clearly reverses the other (auto-flip the older to
    ``superseded`` only above :data:`SUPERSEDE_AUTOFLIP_CONFIDENCE`, otherwise a
    reviewable proposal), or ``conflicts_with`` when two *active* decisions
    contradict with no clear winner.

**3C — two-pass evolution on ``repowise update``** (:func:`run_update_evolution`).
    Pass 1 (cheap, no LLM): scan changed files' diffs + new commit bodies for
    evolution signals (``migrate``/``replace``/``deprecate``/…) and cross-ref
    existing decisions touching those files. Pass 2 (LLM, survivors only,
    gated): ask whether each surviving decision is *amended* / *superseded* /
    *reaffirmed* by the change. The caller runs this **before** page
    regeneration so the governed pages of an amended decision re-render in the
    same run.

Cheap-before-expensive: Pass 1 gates Pass 2 (LLM only on survivors); semantic
comparisons reuse the shared store; edge writes are O(candidates); the lineage
walk that consumes these edges is bounded elsewhere.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from repowise.core.analysis.decisions.gate import apply_substring_gate
from repowise.core.analysis.decisions.provenance import normalize_text
from repowise.core.analysis.decisions.semantic_match import (
    DEFAULT_DEDUP_TAU,
    find_related_decisions,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "EVOLUTION_SIGNALS",
    "RELATED_TAU",
    "SUPERSEDE_AUTOFLIP_CONFIDENCE",
    "contradicts",
    "detect_supersessions_and_conflicts",
    "is_reversal",
    "pass1_evolution_candidates",
    "run_update_evolution",
    "scan_evolution_signals",
    "supersession_confidence",
]

# ---------------------------------------------------------------------------
# Tunable knobs (live next to DEFAULT_DEDUP_TAU; pick empirically on real repos)
# ---------------------------------------------------------------------------

# Lower bound of the "same topic" similarity band. The upper bound is the dedup
# threshold: above it the two would have merged into one record (not a
# supersession); below RELATED_TAU they are unrelated. So a supersession
# candidate sits in ``[RELATED_TAU, DEFAULT_DEDUP_TAU)``.
RELATED_TAU = 0.6

# A detected supersession always records the edge; the *older* decision is only
# auto-flipped to ``superseded`` when confidence clears this bar. Conservative
# by design — everything below stays a reviewable proposal.
SUPERSEDE_AUTOFLIP_CONFIDENCE = 0.85

# ---------------------------------------------------------------------------
# Evolution signals (Pass-1 pattern scan) + contradiction heuristics
# ---------------------------------------------------------------------------

# Substrings that, in a diff or commit body, hint the change *evolves* a prior
# decision rather than just touching code. Kept lowercase; matched as substrings.
EVOLUTION_SIGNALS: frozenset[str] = frozenset(
    {
        "migrate",
        "migrated",
        "migration",
        "replace",
        "replaced",
        "switch to",
        "switched to",
        "move to",
        "moved to",
        "move away",
        "moved away",
        "chose",
        "choose",
        "because",
        "instead of",
        "rather than",
        "deprecate",
        "deprecated",
        "revert",
        "reverted",
        "no longer",
        "stop using",
        "drop ",
        "dropped ",
        "in favor of",
        "in favour of",
    }
)

# Directional reversal phrases: their presence in the *newer* decision means it
# reverses/replaces a predecessor (→ supersedes, not merely conflicts).
_REVERSAL_SIGNALS: tuple[str, ...] = (
    "replace",
    "replaced",
    "migrate",
    "migrated",
    "migration",
    "switch to",
    "switched to",
    "move away",
    "moved away",
    "move to",
    "moved to",
    "revert",
    "reverted",
    "deprecate",
    "deprecated",
    "no longer",
    "stop using",
    "instead of",
    "in favor of",
    "in favour of",
)

# Opposing verb/intent pairs. A pair where one side appears in text A and the
# other in text B signals the two decisions push in opposite directions.
_OPPOSING_VERB_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset(
            {"adopt", "adopted", "use", "using", "add", "added", "introduce", "enable", "enabled"}
        ),
        frozenset(
            {
                "drop",
                "dropped",
                "remove",
                "removed",
                "deprecate",
                "deprecated",
                "disable",
                "disabled",
                "revert",
                "reverted",
                "abandon",
            }
        ),
    ),
    (
        frozenset({"switch to", "migrate to", "move to"}),
        frozenset({"revert", "roll back", "rollback", "move back"}),
    ),
    (
        frozenset({"sync", "synchronous", "blocking"}),
        frozenset({"async", "asynchronous", "non-blocking"}),
    ),
    (frozenset({"monolith", "monolithic"}), frozenset({"microservice", "microservices"})),
)

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "and",
        "or",
        "in",
        "on",
        "with",
        "we",
        "our",
        "use",
        "using",
        "should",
        "this",
        "that",
        "is",
        "are",
        "be",
        "by",
        "as",
        "it",
        "its",
        "from",
        "at",
        "via",
        "into",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text: str) -> set[str]:
    """Lowercased content tokens (stopwords + short tokens dropped)."""
    return {
        t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 2 and t not in _STOPWORDS
    }


def _shared_topic(text_a: str, text_b: str, *, min_shared: int = 2) -> bool:
    """Do two texts share enough content tokens to be 'about the same thing'?"""
    return len(_content_tokens(text_a) & _content_tokens(text_b)) >= min_shared


def scan_evolution_signals(text: str) -> list[str]:
    """Return the evolution signals present in *text* (Pass-1 cheap filter)."""
    low = normalize_text(text)
    if not low:
        return []
    return sorted(s for s in EVOLUTION_SIGNALS if s.strip() in low)


def is_reversal(text: str) -> tuple[bool, str]:
    """Does *text* read like it reverses/replaces a prior choice? → (bool, signal)."""
    low = normalize_text(text)
    for sig in _REVERSAL_SIGNALS:
        if sig in low:
            return True, sig
    return False, ""


def contradicts(text_a: str, text_b: str) -> tuple[bool, str]:
    """Heuristic: do two decision texts push in opposite directions?

    True when (a) an opposing verb pair straddles the two texts, or (b) either
    text carries a directional reversal signal — *and* the two share enough
    content tokens to be about the same topic (so "deprecate X" and "adopt Y"
    for unrelated X/Y don't false-positive). Returns ``(bool, signal)``.
    """
    if not _shared_topic(text_a, text_b):
        return False, ""
    low_a = normalize_text(text_a)
    low_b = normalize_text(text_b)

    for left, right in _OPPOSING_VERB_PAIRS:
        a_left = any(v in low_a for v in left)
        b_right = any(v in low_b for v in right)
        a_right = any(v in low_a for v in right)
        b_left = any(v in low_b for v in left)
        if (a_left and b_right) or (a_right and b_left):
            return True, "opposing-verbs"

    rev_a, sig_a = is_reversal(text_a)
    if rev_a:
        return True, sig_a
    rev_b, sig_b = is_reversal(text_b)
    if rev_b:
        return True, sig_b
    return False, ""


def supersession_confidence(
    similarity: float,
    *,
    verified: bool = False,
    has_reversal_signal: bool = False,
) -> float:
    """Confidence that one decision supersedes another. Bounded ``[0, 0.99]``.

    Rises with semantic similarity, bumped when the newer decision carries an
    explicit reversal signal and when its headline is a verified quote. Kept
    deliberately conservative around :data:`SUPERSEDE_AUTOFLIP_CONFIDENCE` so a
    borderline match stays a proposal rather than auto-flipping the older one.
    """
    conf = 0.45 + 0.4 * max(0.0, min(1.0, similarity))
    if has_reversal_signal:
        conf += 0.1
    if verified:
        conf += 0.05
    return round(max(0.0, min(0.99, conf)), 3)


# ---------------------------------------------------------------------------
# 3B — semantic supersession & conflict detection
# ---------------------------------------------------------------------------


def _created_at(rec: Any) -> Any:
    return getattr(rec, "created_at", None)


async def detect_supersessions_and_conflicts(
    session: Any,
    repository_id: str,
    *,
    touched_ids: list[str],
    vector_store: Any | None = None,
    provider: Any | None = None,
    autoflip_confidence: float = SUPERSEDE_AUTOFLIP_CONFIDENCE,
) -> dict[str, int]:
    """Record supersedes/conflicts edges for the just-upserted decisions.

    For each touched decision, find existing decisions in the "same topic" band
    and, where they contradict, write a typed :class:`DecisionEdge`. ``supersedes``
    edges auto-flip the older decision to ``superseded`` only above
    *autoflip_confidence*. Best-effort: requires *vector_store* (the semantic
    signal); returns zero-counts and does nothing without it. *provider*, when
    supplied, is a gated tiebreaker for pairs the heuristic misses.

    Returns ``{"supersedes": n, "conflicts": n, "flipped": n}``.
    """
    summary = {"supersedes": 0, "conflicts": 0, "flipped": 0}
    if not touched_ids or vector_store is None:
        return summary

    from datetime import UTC, datetime

    from repowise.core.persistence.decision_graph import upsert_decision_edge
    from repowise.core.persistence.models import DecisionRecord

    # Avoid recording both (A supersedes B) and (B supersedes A) within one run.
    handled_pairs: set[frozenset[str]] = set()

    for tid in touched_ids:
        rec = await session.get(DecisionRecord, tid)
        if rec is None or rec.repository_id != repository_id:
            continue
        related = await find_related_decisions(
            vector_store,
            title=rec.title,
            decision=rec.decision or "",
            lo=RELATED_TAU,
            hi=DEFAULT_DEDUP_TAU,
            exclude_ids={rec.id},
        )
        for other_id, sim in related:
            pair = frozenset({rec.id, other_id})
            if pair in handled_pairs:
                continue
            other = await session.get(DecisionRecord, other_id)
            if other is None or other.repository_id != repository_id:
                continue

            text_new = f"{rec.title}. {rec.decision}"
            text_old = f"{other.title}. {other.decision}"
            contra, signal = contradicts(text_new, text_old)
            if not contra and provider is not None:
                contra, signal = await _llm_contradiction_judge(
                    provider, text_a=text_new, text_b=text_old
                )
            if not contra:
                continue
            handled_pairs.add(pair)

            # Temporal order decides direction: the newer decision supersedes.
            ca_rec, ca_other = _created_at(rec), _created_at(other)
            if ca_rec is not None and ca_other is not None and ca_other > ca_rec:
                newer, older = other, rec
            else:
                newer, older = rec, other

            rev, rev_sig = is_reversal(f"{newer.title}. {newer.decision}")
            conf = supersession_confidence(
                sim,
                verified=(newer.verification == "exact"),
                has_reversal_signal=rev,
            )

            if rev or older.status not in ("active",) or newer.status not in ("active",):
                # A clear reversal, or at least one side not co-active → model
                # it as supersession (newer over older).
                edge = await upsert_decision_edge(
                    session,
                    repository_id=repository_id,
                    src_decision_id=newer.id,
                    dst_decision_id=older.id,
                    kind="supersedes",
                    confidence=conf,
                    evidence=f"auto-detected: {signal or rev_sig or 'semantic'} (sim={sim:.2f})",
                )
                if edge is not None:
                    summary["supersedes"] += 1
                    if conf >= autoflip_confidence and older.status in ("active", "proposed"):
                        older.status = "superseded"
                        older.superseded_by = newer.id
                        older.updated_at = datetime.now(UTC)
                        summary["flipped"] += 1
            else:
                # Two active decisions contradict, neither clearly reverses the
                # other → a governance smell, surfaced in health.
                edge = await upsert_decision_edge(
                    session,
                    repository_id=repository_id,
                    src_decision_id=newer.id,
                    dst_decision_id=older.id,
                    kind="conflicts_with",
                    confidence=conf,
                    evidence=f"auto-detected: {signal or 'semantic'} (sim={sim:.2f})",
                )
                if edge is not None:
                    summary["conflicts"] += 1

    await session.flush()
    return summary


_CONTRADICTION_SYSTEM = (
    "You judge whether two architectural decisions contradict each other. "
    "Answer with a single word: CONTRADICT or COMPATIBLE."
)


async def _llm_contradiction_judge(provider: Any, *, text_a: str, text_b: str) -> tuple[bool, str]:
    """Gated LLM tiebreaker: do two decisions contradict? Best-effort → (bool, signal)."""
    prompt = (
        "Decision A:\n"
        f"{text_a}\n\n"
        "Decision B:\n"
        f"{text_b}\n\n"
        "Do these two decisions contradict each other (one reverses, replaces, "
        "or is incompatible with the other)? Answer CONTRADICT or COMPATIBLE."
    )
    try:
        response = await provider.generate(
            _CONTRADICTION_SYSTEM, prompt, max_tokens=8, temperature=0.0
        )
    except Exception:
        return False, ""
    verdict = (response.content or "").strip().upper()
    if verdict.startswith("CONTRADICT"):
        return True, "llm-judge"
    return False, ""


# ---------------------------------------------------------------------------
# 3C — two-pass evolution on update
# ---------------------------------------------------------------------------


def pass1_evolution_candidates(
    changed_files: set[str],
    evidence_by_file: dict[str, str],
    existing_decisions: list[Any],
) -> list[tuple[Any, str, list[str]]]:
    """Pass 1 (no LLM): existing decisions whose governed files changed *with*
    an evolution signal in the change evidence.

    *evidence_by_file* maps a changed file path to the text to scan (its diff +
    triggering commit subject/body — NOT the whole file, to keep ``because`` /
    ``instead of`` from firing on every source file). Returns
    ``[(decision, evidence_text, signals)]`` — the survivors handed to Pass 2.
    """
    out: list[tuple[Any, str, list[str]]] = []
    for dec in existing_decisions:
        try:
            affected = set(json.loads(dec.affected_files_json or "[]"))
        except (TypeError, ValueError):
            affected = set()
        touched = affected & changed_files
        if not touched:
            continue
        signals: set[str] = set()
        parts: list[str] = []
        for f in sorted(touched):
            evidence = evidence_by_file.get(f, "")
            sigs = scan_evolution_signals(evidence)
            if sigs:
                signals.update(sigs)
                parts.append(evidence[:2000])
        if signals:
            out.append((dec, "\n\n".join(parts), sorted(signals)))
    return out


_EVOLUTION_JUDGE_SYSTEM = (
    "You assess whether a code change evolves a previously recorded "
    "architectural decision. Return only valid JSON. "
    "Never invent rationale not present in the change evidence."
)

_EVOLUTION_JUDGE_PROMPT = """\
An existing architectural decision and a recent code change are given below. \
Decide how the change affects the decision.

EXISTING DECISION
Title: {title}
Decision: {decision}
Rationale: {rationale}

CHANGE EVIDENCE (diff + commit messages)
{evidence}

Respond with a JSON object:
{{"verdict": "superseded" | "amended" | "reaffirmed" | "none",
  "rationale": "<one sentence quoting the change evidence>",
  "source_quote": "<verbatim span from the change evidence supporting the verdict>"}}

- "superseded": the change reverses/replaces the decision.
- "amended": the change modifies the decision without fully reversing it.
- "reaffirmed": the change is consistent with and strengthens the decision.
- "none": the change does not affect the decision.
Quote only text that appears verbatim in the change evidence."""


class _JudgeCandidate:
    """Minimal duck-typed shape for :func:`apply_substring_gate`."""

    def __init__(self, *, rationale: str, source_quote: str, source_text: str) -> None:
        self.decision = ""
        self.rationale = rationale
        self.source_quote = source_quote
        self.source_text = source_text
        self.verification = "unverified"


async def _judge_evolution(
    provider: Any,
    *,
    title: str,
    decision: str,
    rationale: str,
    evidence_text: str,
) -> dict | None:
    """Pass 2: ask the LLM how the change affects one decision (gated).

    Any rationale/quote the LLM produces must substring/token-match the change
    evidence (the same anti-hallucination gate the extractor + harvest use) —
    an ungrounded verdict is discarded. Returns the parsed, gated verdict dict
    or ``None``.
    """
    prompt = _EVOLUTION_JUDGE_PROMPT.format(
        title=title,
        decision=decision,
        rationale=rationale,
        evidence=evidence_text[:6000],
    )
    try:
        response = await provider.generate(
            _EVOLUTION_JUDGE_SYSTEM, prompt, max_tokens=400, temperature=0.1
        )
    except Exception:
        return None

    parsed = _parse_judge_json(response.content)
    if not parsed:
        return None
    verdict = (parsed.get("verdict") or "none").strip().lower()
    if verdict not in ("superseded", "amended", "reaffirmed"):
        return None

    # Gate the produced rationale/quote against the change evidence.
    cand = _JudgeCandidate(
        rationale=parsed.get("rationale") or "",
        source_quote=parsed.get("source_quote") or "",
        source_text=evidence_text,
    )
    kept, _ = apply_substring_gate([cand])
    if not kept:
        # Reaffirmed verdicts need no grounded quote (they assert "nothing
        # changed"); supersede/amend must be evidence-backed.
        if verdict == "reaffirmed":
            return {
                "verdict": verdict,
                "rationale": "",
                "source_quote": "",
                "verification": "unverified",
            }
        return None
    survivor = kept[0]
    return {
        "verdict": verdict,
        "rationale": survivor.rationale,
        "source_quote": survivor.source_quote,
        "verification": survivor.verification,
    }


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_judge_json(content: str | None) -> dict | None:
    if not content:
        return None
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        pass
    match = _JSON_BLOCK_RE.search(content)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


async def run_update_evolution(
    session: Any,
    repository_id: str,
    *,
    changed_files: set[str],
    evidence_by_file: dict[str, str],
    provider: Any | None = None,
) -> dict[str, Any]:
    """Two-pass evolution for ``repowise update``, run *before* page regen.

    Returns ``{"regen_files": set[str], "superseded": n, "amended": n,
    "reaffirmed": n}``. ``regen_files`` are the governed files of every decision
    the change superseded or amended — the caller folds them into the
    regeneration set so they re-render in the same run.

    Pass 1 always runs (cheap). Pass 2 needs a *provider*; without one the
    survivors' governed pages are still scheduled for regen (the signal alone is
    enough to warrant a refresh) but no status change is made.
    """
    from datetime import UTC, datetime

    from repowise.core.persistence.crud import list_decisions

    result: dict[str, Any] = {
        "regen_files": set(),
        "superseded": 0,
        "amended": 0,
        "reaffirmed": 0,
    }
    if not changed_files:
        return result

    existing = await list_decisions(session, repository_id, include_proposed=True, limit=500)
    candidates = pass1_evolution_candidates(changed_files, evidence_by_file, existing)
    if not candidates:
        return result

    regen: set[str] = result["regen_files"]
    now = datetime.now(UTC)

    for dec, evidence_text, _signals in candidates:
        try:
            governed = list(json.loads(dec.affected_files_json or "[]"))
        except (TypeError, ValueError):
            governed = []

        if provider is None:
            # No LLM available: a signal touched a governed file — re-render the
            # governed pages so docs stay fresh, but don't change status.
            regen.update(governed)
            continue

        verdict = await _judge_evolution(
            provider,
            title=dec.title,
            decision=dec.decision,
            rationale=dec.rationale,
            evidence_text=evidence_text,
        )
        if verdict is None:
            continue

        kind = verdict["verdict"]
        if kind == "superseded":
            if dec.status in ("active", "proposed"):
                dec.status = "superseded"
                dec.updated_at = now
            regen.update(governed)
            result["superseded"] += 1
        elif kind == "amended":
            # Keep it active but mark it stale so its drift is visible, and
            # re-render its governed pages to reflect the amendment.
            dec.staleness_score = max(dec.staleness_score, 0.6)
            dec.updated_at = now
            regen.update(governed)
            result["amended"] += 1
        elif kind == "reaffirmed":
            # Still holds — relax staleness; no regen needed.
            dec.staleness_score = min(dec.staleness_score, 0.2)
            dec.updated_at = now
            result["reaffirmed"] += 1

    await session.flush()
    return result
