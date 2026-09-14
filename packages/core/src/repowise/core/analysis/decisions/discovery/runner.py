"""The one broad discovery call an update is allowed to make.

Everything here is arranged so that "at most one call" is a property of the
code rather than a promise: there is exactly one ``provider.generate`` in this
module, it is not in a loop, and every path that must not call reaches its
``return`` above it carrying the reason it stopped.

Output is review-only. Candidates land in the session staging store and reach
``decision_records`` through the same promotion path as the deterministic
lane, which writes ``proposed`` and nothing else.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.discovery.grounding import (
    GroundedCandidate,
    ground_candidates,
    parse_response,
)
from repowise.core.analysis.decisions.discovery.packet import (
    SYSTEM_PROMPT,
    build_packet,
)
from repowise.core.analysis.decisions.discovery.spans import ProseSpan
from repowise.core.analysis.decisions.policy import DecisionPolicy
from repowise.core.sessions.staging import DISCOVERY_KIND, SessionStagingStore

logger = structlog.get_logger(__name__)

__all__ = ["DISCOVERY_SOURCE", "DiscoveryOutcome", "DiscoveryReport", "run_update_discovery"]

#: The policy registry key. It names a *capture lane*, not a stored record
#: source: promoted candidates keep ``source="session"`` because that is what
#: the ranking ladder and every stored row already mean by a transcript.
DISCOVERY_SOURCE = "session_discovery"

#: Output ceiling for one call. The probe's 16-candidate response spent about
#: 5,000 tokens, and a truncated reply is unparseable rather than partial.
_MAX_OUTPUT_TOKENS = 6_000

_TEMPERATURE = 0.1

#: Spans read off the queue before the budget is applied. A ceiling on the
#: query, not on the packet: the packet's own bounds decide what is sent.
_QUEUE_READ_LIMIT = 2_000


@dataclass(slots=True)
class DiscoveryReport:
    """The funnel for one update, with no transcript text in it.

    ``status`` is the same three-way vocabulary the index reporting already
    uses for every other source: it ran, it was not run and here is why, or it
    failed. A zero from a source that is switched off must never read like a
    zero from a repository with nothing to find.
    """

    status: str = "not_run"
    reason: str = ""
    calls: int = 0
    sessions_considered: int = 0
    spans_considered: int = 0
    spans_deferred: int = 0
    spans_retired: int = 0
    candidates_returned: int = 0
    candidates_grounded: int = 0
    candidates_new: int = 0
    candidates_accreted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    rejected_task_local: int = 0
    rejected_assistant_only: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "calls": self.calls,
            "sessions_considered": self.sessions_considered,
            "spans_considered": self.spans_considered,
            "spans_deferred": self.spans_deferred,
            "spans_retired": self.spans_retired,
            "candidates_returned": self.candidates_returned,
            "candidates_grounded": self.candidates_grounded,
            "candidates_new": self.candidates_new,
            "candidates_accreted": self.candidates_accreted,
            "rejected": dict(self.rejected),
            "rejected_task_local": self.rejected_task_local,
            "rejected_assistant_only": self.rejected_assistant_only,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
        }


@dataclass(slots=True)
class DiscoveryOutcome:
    """The report, plus whatever this pass qualified for promotion."""

    report: DiscoveryReport
    decisions: list[Any] = field(default_factory=list)


def candidate_hash(candidate: GroundedCandidate) -> str:
    """Conservative identity: the claim, plus the evidence it rests on.

    Two extractions of the same claim from the same spans are one candidate;
    the same claim from different evidence is a second observation of it, which
    is what accretes review priority through the staging store's title key.
    """
    spans = "|".join(sorted(span.span_id for span in candidate.spans))
    claim = " ".join(candidate.decision.lower().split())
    return hashlib.sha256(f"{DISCOVERY_KIND}|{claim}|{spans}".encode()).hexdigest()[:16]


async def run_update_discovery(
    repo_path: Path,
    *,
    provider: Any | None,
    policy: DecisionPolicy,
    now: float | None = None,
) -> DiscoveryOutcome:
    """Make zero or one broad discovery call over this update's new prose.

    Runs after the deterministic miner, which is what filled the span queue
    during its single transcript read. Everything it persists is a candidate.
    """
    report = DiscoveryReport()
    if not policy.enabled:
        report.reason = "Decision capture is off for this repository."
        return DiscoveryOutcome(report)
    if not policy.source_enabled(DISCOVERY_SOURCE):
        report.reason = "Broad session discovery is switched off."
        return DiscoveryOutcome(report)
    if not policy.llm_allowed(DISCOVERY_SOURCE):
        report.reason = "Decision LLM extraction is off, and this source has no local stage."
        return DiscoveryOutcome(report)
    if provider is None:
        report.status = "skipped_no_provider"
        report.reason = "No LLM provider is configured."
        return DiscoveryOutcome(report)

    repo_root = Path(repo_path).resolve()
    store = SessionStagingStore.open_default(repo_root)
    try:
        return await _discover(store, repo_root, provider, policy, report, now)
    finally:
        store.close()


async def _discover(
    store: SessionStagingStore,
    repo_root: Path,
    provider: Any,
    policy: DecisionPolicy,
    report: DiscoveryReport,
    now: float | None,
) -> DiscoveryOutcome:
    queued = store.pending_discovery_spans(_QUEUE_READ_LIMIT)
    pending_total = store.pending_discovery_count()
    if not queued:
        report.status = "empty"
        report.reason = (
            "No new session prose since the last update."
            if policy.source_enabled("session")
            else "The agent-sessions source that reads transcripts is switched off."
        )
        return DiscoveryOutcome(report)

    packet = build_packet(
        [ProseSpan.from_row(row) for row in queued],
        max_sessions=policy.discovery.max_sessions,
        max_input_tokens=policy.discovery.max_input_tokens,
    )
    if not packet:
        # Unreachable while build_packet guarantees one surviving span, and
        # kept because the failure it guards is a permanently wedged queue:
        # returning here consumes nothing, so the same head would be rebuilt
        # into the same empty packet on every future update.
        report.status = "empty"
        report.reason = "No session prose fit the discovery budget."
        return DiscoveryOutcome(report)

    report.sessions_considered = len(packet.sessions)
    report.spans_considered = len(packet.spans)
    report.spans_deferred = max(0, pending_total - len(packet.spans))
    span_ids = [span.span_id for span in packet.spans]

    started = time.monotonic()
    try:
        response = await provider.generate(
            SYSTEM_PROMPT,
            packet.prompt,
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=_TEMPERATURE,
        )
    except Exception as exc:
        # Transient by assumption: the spans stay queued and the next update
        # retries them, bounded, so a provider outage costs a round rather
        # than the input.
        report.status = "failed"
        report.reason = str(exc)
        report.spans_retired = store.bump_discovery_attempts(span_ids, now=now)
        report.spans_deferred = pending_total - report.spans_retired
        store.commit()
        logger.warning("decision_discovery.llm_failed", error=str(exc))
        return DiscoveryOutcome(report)

    report.calls = 1
    report.latency_ms = int((time.monotonic() - started) * 1000)
    report.input_tokens = getattr(response, "input_tokens", 0) or 0
    report.output_tokens = getattr(response, "output_tokens", 0) or 0

    grounding = ground_candidates(
        parse_response(response.content), packet.spans, packet.known_paths
    )
    report.candidates_returned = grounding.returned
    report.candidates_grounded = len(grounding.grounded)
    report.rejected = dict(grounding.rejected)
    report.rejected_task_local = grounding.rejected_task_local
    report.rejected_assistant_only = grounding.rejected_assistant_only
    report.status = "ran" if grounding.grounded else "empty"
    if report.status == "empty":
        report.reason = "The pass found no grounded candidate in this update's prose."

    # The spans were read, so they are spent whatever the model made of them.
    # A candidate the gate rejected is a permanent rejection, not a retry.
    store.mark_discovery_consumed(span_ids, now=now)
    _persist(store, grounding.grounded, report, now)
    store.commit()

    decisions = _promote(store, repo_root, now)
    store.commit()

    logger.info("decision_discovery.done", **report.to_dict())
    return DiscoveryOutcome(report, decisions)


def _persist(
    store: SessionStagingStore,
    candidates: list[GroundedCandidate],
    report: DiscoveryReport,
    now: float | None,
) -> None:
    for candidate in candidates:
        # Accretion is a property of the claim, not of the evidence: spans are
        # spent after one packet, so the raw hash is always new and only the
        # title key can say this claim has been seen before.
        if store.structured_exists(candidate.title, DISCOVERY_KIND):
            report.candidates_accreted += 1
        else:
            report.candidates_new += 1
        raw_hash = candidate_hash(candidate)
        store.add_raw(
            hash_=raw_hash,
            kind=DISCOVERY_KIND,
            quotes=candidate.quotes,
            files=list(candidate.affected_files),
            session_id=candidate.session_id,
            now=now,
        )
        store.upsert_structured(
            raw_hash,
            kind=DISCOVERY_KIND,
            lane=DISCOVERY_KIND,
            title=candidate.title,
            structured=candidate.to_structured(),
            quotes=candidate.quotes,
            files=list(candidate.affected_files),
            session_id=candidate.session_id,
            now=now,
        )


def _promote(store: SessionStagingStore, repo_root: Path, now: float | None) -> list[Any]:
    """Emit newly qualified discovery rows through the shared promotion path.

    Restricted to this lane's own rows: the deterministic miner runs its own
    promotion pass and owns its backlog, and a discovery run must not emit a
    gate hit under the discovery lane's name because it happened to go second.

    Imported here rather than at module scope: the miner imports this package
    to collect spans, so a top-level import back into it would be a cycle.
    """
    from repowise.core.sessions.miners.decisions import promotion_decisions

    decisions: list[Any] = []
    for row in store.promotable():
        if row["kind"] != DISCOVERY_KIND:
            continue
        decisions.extend(promotion_decisions(row, repo_root))
        store.mark_emitted(row["key"], observations=row["observations"], now=now)
    return decisions
