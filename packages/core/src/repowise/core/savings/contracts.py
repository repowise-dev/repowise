"""Versioned, validated contracts for savings events and reports."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from repowise.core.agents.identity import UNKNOWN_AGENT, is_agent_slug
from repowise.core.savings.correlation import (
    file_session_key,
    hash_correlation_evidence,
    new_event_id,
)
from repowise.core.savings.formulas import TokenAccounting, calculate_token_accounting
from repowise.core.savings.normalization import normalize_metadata

if TYPE_CHECKING:
    # Type-only: the event writers import this module on the hook path, and
    # the estimate is a report-time concern they never touch.
    from repowise.core.savings.output_estimate import OutputEstimate

SCHEMA_VERSION = 1
SURFACES = frozenset({"distill", "hook", "mcp", "vscode_lm"})
EVIDENCE_KINDS = frozenset({"measured", "inferred"})
RESULT_STATES = frozenset({"success", "dead_end", "error", "partial", "unknown"})
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
#: Files one event may credit as not opened. A ``get_context`` call with many
#: file targets or a long ``get_answer`` citation list stops here; the token
#: credit is unaffected, only the per-file list is cut. Paths, never contents.
MAX_AVOIDED_FILES = 50
_MAX_AVOIDED_PATH_CHARS = 512


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


def _avoided_files(value: Mapping[str, Any]) -> tuple[tuple[str, int | None], ...]:
    """``(path, read_tokens)`` pairs this event served in place of a Read.

    Best-effort by design: a malformed entry is skipped, never raised, because
    the list rides on an event whose token saving must not be lost over it.
    First entry per path wins; the list is capped at :data:`MAX_AVOIDED_FILES`.
    """
    raw = value.get("avoided_files") or ()
    items = raw.items() if isinstance(raw, Mapping) else raw
    files: dict[str, int | None] = {}
    for item in items:
        if len(files) >= MAX_AVOIDED_FILES:
            break
        try:
            path, tokens = item
        except (TypeError, ValueError):
            continue
        if not isinstance(path, str) or not path or len(path) > _MAX_AVOIDED_PATH_CHARS:
            continue
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            tokens = None
        files.setdefault(path, tokens)
    return tuple(files.items())


#: What a check tool's reply returned, as a few counts and one or two enums.
#: The vocabulary is closed so the column stays small and readable by the
#: report; anything else is dropped. Each tool uses a subset (see the MCP
#: wrapper's ``_savings/outcome.py``).
OUTCOME_LEVELS = ("high", "medium", "low")
OUTCOME_DECISIONS = ("accepted", "candidate", "none")
_OUTCOME_ENUMS: dict[str, tuple[str, ...]] = {
    "level": OUTCOME_LEVELS,
    "decision": OUTCOME_DECISIONS,
}
_OUTCOME_COUNTS = frozenset(
    {"high", "medium", "low", "findings", "files", "items", "high_confidence"}
)
_OUTCOME_COUNT_MAX = 1_000_000


def normalize_outcome(value: object) -> dict[str, Any] | None:
    """*value* reduced to the outcome vocabulary, or ``None`` when it is not a mapping.

    Never raises: an outcome is an annotation, and a bad one costs only itself.
    An empty mapping is kept as ``{}``: the check ran and returned nothing to count.
    """
    if not isinstance(value, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in _OUTCOME_ENUMS and item in _OUTCOME_ENUMS[key]:
            out[key] = item
        elif (
            key in _OUTCOME_COUNTS
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        ):
            out[key] = min(item, _OUTCOME_COUNT_MAX)
    return out


def _file_session(value: Mapping[str, Any]) -> str | None:
    """The cross-surface session key for this event's files, or None."""
    raw = value.get("host_session_id")
    if raw is None or raw == "":
        return None
    raw = str(raw)
    return raw if _HASH_RE.fullmatch(raw) else file_session_key(raw)


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
    #: Files this event served in place of a Read, with what one Read of each
    #: costs (None when unsized). Stored beside the event, not in its row.
    avoided_files: tuple[tuple[str, int | None], ...] = ()
    #: :func:`~repowise.core.savings.correlation.file_session_key` of the host
    #: session, so a file is credited once per session across surfaces.
    file_session_key: str | None = None
    #: What a check tool returned (:func:`normalize_outcome`); ``None`` for
    #: every other operation and for events recorded before outcomes were kept.
    outcome: Mapping[str, Any] | None = None

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
        try:
            # Best-effort: a bad file list costs the list, never the event.
            avoided = _avoided_files(value) if semantics.is_usable else ()
            file_key = _file_session(value)
        except Exception:
            avoided, file_key = (), None
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
            avoided_files=avoided,
            file_session_key=file_key,
            outcome=normalize_outcome(value.get("outcome")),
        )

    def metadata_json(self) -> str:
        return json.dumps(dict(self.metadata), sort_keys=True, separators=(",", ":"))

    def outcome_json(self) -> str | None:
        if self.outcome is None:
            return None
        return json.dumps(dict(self.outcome), sort_keys=True, separators=(",", ":"))


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
    #: How much smaller the input got. The headline total answers "how many
    #: tokens"; this answers "out of how many", which is the only form in which
    #: one repository's savings compare with another's.
    #:
    #: Two populations, and both ship because either alone misleads.
    #: ``baseline_events`` counts every interaction that had something to
    #: compare against; ``reducing_events`` counts the subset where the input
    #: actually got smaller. The ratio below is over the subset -- "when it
    #: fires, by how much" -- and is only honest while the surface states the
    #: coverage beside it, which is what the two counts are for. An event with
    #: no baseline at all is in neither: it is not a reduction of nought.
    baseline_events: int = 0
    reducing_events: int = 0
    #: Both sides of the ratio, over ``reducing_events``, so the percentage is
    #: checkable rather than asserted.
    baseline_input_tokens: int = 0
    baseline_saved_input_tokens: int = 0
    #: ``baseline_saved_input_tokens / baseline_input_tokens``, and the
    #: nearest-rank 90th percentile of the same ratio taken per event. Both
    #: null when nothing in the window reduced anything. The aggregate is what
    #: a reduction is typically worth; the percentile says how far it goes on
    #: the outputs where it matters, and one without the other is a half-truth
    #: in whichever direction flatters.
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
    #: Distinct (session, path) pairs a usable event served in place of a Read,
    #: each credited once per session to the first event that served it, and
    #: cancelled when the agent Read the path later in that session. Tokens are
    #: one Read of each, as measured when the event was recorded.
    files_not_opened: int = 0
    files_not_opened_tokens: int = 0
    #: Tool calls Repowise answered: usable MCP, VS Code tool and hook events.
    tool_calls_answered: int = 0
    #: Rows of ``{day|agent|surface, files_not_opened, files_not_opened_tokens,
    #: tool_calls_answered}``; a credited file counts under its owning event.
    files_per_day: tuple[Mapping[str, Any], ...] = ()
    files_per_agent: tuple[Mapping[str, Any], ...] = ()
    files_per_surface: tuple[Mapping[str, Any], ...] = ()
    #: The share of the window recorded inside a subagent (events with a
    #: ``subagent_id``). Part of the totals above, never added to them.
    subagent_events: int = 0
    subagent_saved_input_tokens: int = 0
    #: Unpriced tokens priced at report time, never written back to an event.
    #: The rate is the newest one an event of the same session (and surface,
    #: since session ids are hashed per surface) captured, else
    #: the cached rate for the event's agent. A subset of
    #: ``unpriced_saved_input_tokens``, which keeps its meaning (no rate was
    #: captured when the event happened), and reported apart from
    #: ``priced_input_savings_usd`` so a reader can tell captured from
    #: backfilled dollars.
    backfilled_saved_input_tokens: int = 0
    backfilled_input_savings_usd: float = 0.0
    #: Output tokens saved, estimated per session from the paired benchmark
    #: (see :mod:`repowise.core.savings.output_estimate`). Kept apart from the
    #: recorded ``saved_output_tokens``, which no producer measures. Null when
    #: no calibration could be loaded.
    output_estimate: OutputEstimate | None = None
    #: One row per check tool (:data:`~repowise.core.savings.reporting.CHECK_TOOLS`):
    #: usable MCP calls and what they returned. Outcome fields are null when no
    #: call in the window recorded one.
    checks: tuple[Mapping[str, Any], ...] = ()
