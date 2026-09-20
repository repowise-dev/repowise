"""Versioned, validated contracts for savings events and reports."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from repowise.core.agents.identity import UNKNOWN_AGENT, is_agent_slug
from repowise.core.savings.correlation import hash_correlation_evidence, new_event_id
from repowise.core.savings.formulas import TokenAccounting, calculate_token_accounting
from repowise.core.savings.normalization import normalize_metadata

SCHEMA_VERSION = 1
SURFACES = frozenset({"distill", "hook", "mcp", "vscode_lm"})
EVIDENCE_KINDS = frozenset({"measured", "inferred"})
RESULT_STATES = frozenset({"success", "dead_end", "error", "partial", "unknown"})
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _agent_slug(value: object | None, name: str) -> str:
    """Validate a stored agent id syntactically, never by membership.

    Deliberately not a closed set. A closed set here would be a second copy of
    the agent registry: it would go stale the day a seventh agent landed and
    bucket that agent's traffic as unattributed forever, and an event written by
    an agent since retired would stop reading back at all. Whether an announced
    name is *recognised* is a question for
    :mod:`repowise.core.agents.identity`, asked once when a client announces
    itself — not again on every read of a value we ourselves wrote.

    Applied identically on the way in and on the way out, which is the point:
    the two used to disagree, raising on an unrecognised id while the report
    silently coerced one to ``unknown``.
    """
    slug = str(value if value is not None else UNKNOWN_AGENT)
    if not is_agent_slug(slug):
        raise ValueError(f"{name} must be a bounded lowercase agent slug: {slug!r}")
    return slug


def utc_text(value: str | datetime) -> str:
    """Normalize an aware timestamp to a lexically sortable UTC string."""
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _bounded(value: object | None, name: str, limit: int = 128) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string of at most {limit} characters")
    return value


def _correlation(
    value: object | None, *, kind: str, repository_id: str, surface: str
) -> str | None:
    bounded = _bounded(value, f"{kind}_id", 128)
    if bounded is None or _HASH_RE.fullmatch(bounded):
        return bounded
    return hash_correlation_evidence(f"{repository_id}:{surface}:{kind}", bounded)


@dataclass(frozen=True, slots=True)
class _Semantics:
    surface: str
    evidence_kind: str
    result_state: str
    integration: str
    agent: str
    is_usable: bool


@dataclass(frozen=True, slots=True)
class _Pricing:
    confidence: float | None
    model: str | None
    currency: str | None
    source: str | None
    version: str | None
    input_rate: float | None
    output_rate: float | None


def _event_identity(value: Mapping[str, Any], accept_event_id: bool) -> tuple[str, str]:
    event_id = value.get("event_id") if accept_event_id else None
    event_id = str(event_id or new_event_id())
    UUID(event_id)
    idempotency_key = str(value["idempotency_key"])
    if not _HASH_RE.fullmatch(idempotency_key):
        raise ValueError("idempotency_key must be a sha256 digest")
    return event_id, idempotency_key


def _semantics(value: Mapping[str, Any]) -> _Semantics:
    surface = str(value["surface"])
    evidence_kind = str(value["evidence_kind"])
    result_state = str(value["result_state"])
    integration = _agent_slug(value.get("integration"), "integration")
    agent = _agent_slug(value.get("agent"), "agent")
    if surface not in SURFACES:
        raise ValueError(f"unsupported surface: {surface}")
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"unsupported evidence kind: {evidence_kind}")
    if result_state not in RESULT_STATES:
        raise ValueError(f"unsupported result state: {result_state}")
    is_usable = value.get("is_usable")
    if not isinstance(is_usable, bool):
        raise ValueError("is_usable must be explicit")
    expected_unusable = result_state in {"dead_end", "error", "unknown"}
    if (result_state == "success" and not is_usable) or (expected_unusable and is_usable):
        raise ValueError("is_usable conflicts with result_state")
    return _Semantics(surface, evidence_kind, result_state, integration, agent, is_usable)


def _accounting(value: Mapping[str, Any], semantics: _Semantics) -> TokenAccounting:
    accounting = calculate_token_accounting(
        surface=semantics.surface,
        evidence_kind=semantics.evidence_kind,
        result_state=semantics.result_state,
        is_usable=semantics.is_usable,
        baseline_input_tokens=value.get("baseline_input_tokens"),
        pre_budget_input_tokens=value.get("pre_budget_input_tokens"),
        delivered_input_tokens=value.get("delivered_input_tokens"),
        baseline_output_tokens=value.get("baseline_output_tokens"),
        delivered_output_tokens=value.get("delivered_output_tokens"),
    )
    for name in ("dropped_input_tokens", "saved_input_tokens", "saved_output_tokens"):
        if name in value and value[name] != getattr(accounting, name):
            raise ValueError(f"{name} does not match the v1 formula")
    return accounting


def _pricing(value: Mapping[str, Any]) -> _Pricing:
    confidence = value.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
    input_value = value.get("input_rate_usd_per_million")
    output_value = value.get("output_rate_usd_per_million")
    input_rate = float(input_value) if input_value is not None else None
    output_rate = float(output_value) if output_value is not None else None
    rates = (input_rate, output_rate)
    if any(rate is not None and (not math.isfinite(rate) or rate < 0) for rate in rates):
        raise ValueError("pricing rates must be finite and nonnegative")
    currency = _bounded(value.get("currency"), "currency", 3)
    source = _bounded(value.get("pricing_source"), "pricing_source", 64)
    version = _bounded(value.get("pricing_version"), "pricing_version", 64)
    model = _bounded(value.get("model"), "model", 128)
    if any(rate is not None for rate in rates) and (
        currency != "USD" or not model or not source or not version
    ):
        raise ValueError("priced events require model and a complete USD pricing snapshot")
    return _Pricing(confidence, model, currency, source, version, input_rate, output_rate)


def _omission_refs(value: Mapping[str, Any]) -> tuple[str, ...]:
    refs = tuple(dict.fromkeys(value.get("omission_refs") or ()))
    if any(not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{12}", ref) for ref in refs):
        raise ValueError("omission_refs must contain canonical 12-hex references")
    return refs


@dataclass(frozen=True, slots=True)
class SavingsEvent:
    event_id: str
    idempotency_key: str
    occurred_at: str
    repository_id: str
    surface: str
    integration: str
    agent: str
    operation: str
    evidence_kind: str
    confidence: float | None
    estimator: str
    token_unit: str
    result_state: str
    is_usable: bool
    baseline_input_tokens: int | None
    pre_budget_input_tokens: int | None
    delivered_input_tokens: int | None
    dropped_input_tokens: int | None
    saved_input_tokens: int
    baseline_output_tokens: int | None
    delivered_output_tokens: int | None
    saved_output_tokens: int | None
    subagent_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    tool_call_id: str | None = None
    model: str | None = None
    currency: str | None = None
    pricing_source: str | None = None
    pricing_version: str | None = None
    input_rate_usd_per_million: float | None = None
    output_rate_usd_per_million: float | None = None
    omission_refs: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, accept_event_id: bool = False
    ) -> SavingsEvent:
        """Validate and normalize an adapter payload into a canonical event."""
        event_id, idempotency_key = _event_identity(value, accept_event_id)
        semantics = _semantics(value)
        accounting = _accounting(value, semantics)
        pricing = _pricing(value)
        refs = _omission_refs(value)
        repository_id = _bounded(value["repository_id"], "repository_id", 256) or ""
        session_id = _correlation(
            value.get("session_id"),
            kind="session",
            repository_id=repository_id,
            surface=semantics.surface,
        )
        request_id = _correlation(
            value.get("request_id"),
            kind="request",
            repository_id=repository_id,
            surface=semantics.surface,
        )
        tool_call_id = _correlation(
            value.get("tool_call_id"),
            kind="tool_call",
            repository_id=repository_id,
            surface=semantics.surface,
        )

        return cls(
            event_id=event_id,
            idempotency_key=idempotency_key,
            occurred_at=utc_text(value["occurred_at"]),
            repository_id=repository_id,
            surface=semantics.surface,
            integration=semantics.integration,
            agent=semantics.agent,
            operation=_bounded(value["operation"], "operation", 128) or "",
            evidence_kind=semantics.evidence_kind,
            confidence=pricing.confidence,
            estimator=_bounded(value["estimator"], "estimator", 128) or "",
            token_unit=_bounded(value["token_unit"], "token_unit", 64) or "",
            result_state=semantics.result_state,
            is_usable=semantics.is_usable,
            baseline_input_tokens=accounting.baseline_input_tokens,
            pre_budget_input_tokens=accounting.pre_budget_input_tokens,
            delivered_input_tokens=accounting.delivered_input_tokens,
            dropped_input_tokens=accounting.dropped_input_tokens,
            saved_input_tokens=accounting.saved_input_tokens,
            baseline_output_tokens=accounting.baseline_output_tokens,
            delivered_output_tokens=accounting.delivered_output_tokens,
            saved_output_tokens=accounting.saved_output_tokens,
            subagent_id=_bounded(value.get("subagent_id"), "subagent_id"),
            session_id=session_id,
            request_id=request_id,
            tool_call_id=tool_call_id,
            model=pricing.model,
            currency=pricing.currency,
            pricing_source=pricing.source,
            pricing_version=pricing.version,
            input_rate_usd_per_million=pricing.input_rate,
            output_rate_usd_per_million=pricing.output_rate,
            omission_refs=refs,
            metadata=normalize_metadata(value.get("metadata")),
        )

    def metadata_json(self) -> str:
        return json.dumps(dict(self.metadata), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class OpportunityObservation:
    observation_id: str
    occurred_at: str
    repository_id: str
    integration: str
    kind: str
    estimated_potential_input_tokens: int

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, repository_id: str | None = None
    ) -> OpportunityObservation:
        integration = _agent_slug(value.get("integration"), "integration")
        tokens = value.get("estimated_potential_input_tokens", 0)
        if isinstance(tokens, bool) or not isinstance(tokens, int):
            raise TypeError("estimated opportunity tokens must be an integer")
        return cls(
            observation_id=_bounded(value["observation_id"], "observation_id", 128) or "",
            occurred_at=utc_text(value["occurred_at"]),
            repository_id=_bounded(repository_id or value["repository_id"], "repository_id", 256)
            or "",
            integration=integration,
            kind=_bounded(value["kind"], "kind", 128) or "",
            estimated_potential_input_tokens=max(tokens, 0),
        )


@dataclass(frozen=True, slots=True)
class SavingsReport:
    """One repository's savings over one window, from one set of queries.

    Every first-party surface -- the costs endpoint, the repository overview
    headline and ``repowise saved`` -- reports from this object and does no
    accounting arithmetic of its own. They used to each aggregate and price the
    ledger independently, and produced three different dollar figures for the
    same repository; that is what this type exists to prevent.

    The breakdowns are bounded tuples, not generators, so a caller cannot turn
    a report into an unbounded payload by iterating harder.
    """

    unique_events: int
    successful_or_usable_partial_events: int
    saving_interactions: int
    mcp_queries_answered: int
    dead_ends: int
    saved_input_tokens: int
    measured_saved_input_tokens: int
    inferred_saved_input_tokens: int
    priced_saved_input_tokens: int
    unpriced_saved_input_tokens: int
    priced_input_savings_usd: float
    saved_output_tokens: int | None
    priced_saved_output_tokens: int
    unpriced_saved_output_tokens: int
    priced_output_savings_usd: float
    opportunity_count: int
    opportunity_tokens_excluded: int
    #: How much smaller the input got, over the events that carry a baseline.
    #: The headline total answers "how many tokens"; this answers "out of how
    #: many", which is the only form in which one repository's savings can be
    #: compared with another's. Events with no baseline are excluded from both
    #: the numerator and the denominator rather than counted as a zero -- an
    #: event with nothing to compare against is not a reduction of nought.
    baseline_events: int = 0
    baseline_input_tokens: int = 0
    baseline_saved_input_tokens: int = 0
    #: ``baseline_saved_input_tokens / baseline_input_tokens``, and the
    #: nearest-rank 90th percentile of the same ratio taken per event. Both
    #: null when no event in the window carried a baseline. The aggregate is
    #: what the repository did overall; the percentile says how far the
    #: reduction goes on the outputs where it matters, and one without the
    #: other is a half-truth in whichever direction flatters.
    input_reduction_ratio: float | None = None
    input_reduction_ratio_p90: float | None = None
    per_operation: tuple[Mapping[str, Any], ...] = ()
    per_surface: tuple[Mapping[str, Any], ...] = ()
    #: Rows carry ``agent_display_name`` beside the slug, resolved from the
    #: identity registry here so no consumer needs a label map of its own --
    #: there were three of those before this, one of them in TypeScript.
    per_agent: tuple[Mapping[str, Any], ...] = ()
    #: ``model`` is null on an unpriced event, which is a real bucket rather
    #: than a gap to hide: it is how much saving carries no rate evidence.
    per_model: tuple[Mapping[str, Any], ...] = ()
    per_day: tuple[Mapping[str, Any], ...] = ()
    per_opportunity_kind: tuple[Mapping[str, Any], ...] = ()
    #: Freshness, for saying how current the figures are rather than implying
    #: they are live. Null when the window holds no events at all.
    first_event_at: str | None = None
    last_event_at: str | None = None
