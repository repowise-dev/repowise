"""Turn a finished MCP invocation into exactly one canonical savings event.

This runs outside every other layer, which is the whole point. The previous
ledger row was written at the savings layer, five of seven layers deep: after
it, ``timed`` stamped ``_meta`` and the outer budget ran the budgeter twice
more, free to shed list entries, replace protected dicts with omission markers
and pop whole keys. So the recorded delivered size was a size the agent never
received, the row overstated what arrived, and anything the outer layer dropped
was invisible to the accounting. Measuring here is measuring the bytes that
actually left.

The evidence split follows the accounting contract exactly. With a
counterfactual the saving is ``replaced - delivered`` and the correlated
truncation is diagnostic only, never added a second time. Without one the
saving is the measured truncation, ``raw - delivered``, and nothing is inferred.
"""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.savings.correlation import scoped_idempotency_key

from .interaction import Interaction

logger = logging.getLogger(__name__)

#: The estimator every repowise savings figure is reported on: floor(chars / 4).
_ESTIMATOR = "chars_per_token_floor_v1"
_TOKEN_UNIT = "estimated_tokens"


def _outcome(result: Any) -> tuple[str, bool]:
    """Classify the delivered response as the contract's result state.

    Three states, and the two that are missing are deliberate. A shaped error is
    an ``error``: it saved nothing, and its delivered overhead is recorded as
    diagnostic rather than as the negative saving the legacy ledger wrote, which
    is how a session that net-spent could still report a credit.

    ``partial`` means content was omitted but is recoverable, so what arrived is
    usable. Both ways of being partial count: a tool that declared itself
    partial at the trust layer, and a response the budgeter shed content from,
    which sets ``state.truncated`` later and is the far more common case.
    Reporting a truncated response as a plain success would credit the bytes
    repowise discarded to a call the agent received incomplete, with nothing in
    the row saying so. The saving is unchanged either way -- the contract gives
    a usable partial the same formula -- so this is about what the row admits,
    not about the number.

    ``dead_end`` is not produced here: a call that succeeds while answering
    nothing needs a per-tool notion of "answered", and inventing one from the
    response shape would be guessing. A tool that knows it dead-ended can say
    so; until one does, such a call is a success that saved zero, which is what
    the contract says a zero-delta success is.
    """
    if not isinstance(result, dict):
        return "success", True
    if result.get("error"):
        return "error", False
    meta = result.get("_meta")
    state = meta.get("state") if isinstance(meta, dict) else None
    if isinstance(state, dict) and (state.get("partial") or state.get("truncated")):
        return "partial", True
    return "success", True


def raw_response_tokens(result: Any) -> int | None:
    """Size an unbudgeted tool response, or ``None`` when it cannot be sized.

    Distinct from the delivered measurement: nothing has stamped a size on this
    payload yet, so it is serialized here, compactly, on the same
    ``floor(chars / 4)`` scale every repowise savings figure uses.
    """
    import json

    from repowise.core.distill.budget import estimate_tokens

    try:
        return estimate_tokens(json.dumps(result, separators=(",", ":"), default=str))
    except Exception:
        return None


def _omission_refs(result: Any) -> tuple[str, ...]:
    """Recovery references this invocation produced, as bare store keys.

    Read off the settled response rather than from the collectors, because a
    single call builds up to four of them across the two budget layers and
    ``attach`` has already merged their refs into one place.

    The response carries them in their public shape, ``repowise#<hex>``, while
    the store keys on the bare hex and the ledger's foreign key expects the
    same. Normalized through the one helper that knows every public shape, so a
    prefixed ref cannot reach validation and silently drop the whole event --
    which it did, on exactly the truncated responses where a saving is largest.
    """
    from repowise.core.distill.markers import normalize_ref

    if not isinstance(result, dict):
        return ()
    meta = result.get("_meta")
    omitted = meta.get("omitted") if isinstance(meta, dict) else None
    refs = omitted.get("refs") if isinstance(omitted, dict) else None
    if not isinstance(refs, list):
        return ()
    normalized = (normalize_ref(ref) for ref in refs if isinstance(ref, str))
    return tuple(ref for ref in normalized if ref is not None)


def record(interaction: Interaction, result: Any) -> bool:
    """Record the one event for *interaction*. Never raises."""
    try:
        return _record(interaction, result)
    except Exception:  # pragma: no cover - accounting never breaks a tool call
        logger.debug("mcp savings event failed for %s", interaction.tool, exc_info=True)
        return False


def _record(interaction: Interaction, result: Any) -> bool:
    from repowise.core.savings import recorder
    from repowise.core.savings.pricing import resolve_pricing_snapshot

    from .wrapper import response_tokens

    if not interaction.repo_root:
        return False

    delivered = response_tokens(result)
    result_state, is_usable = _outcome(result)
    baseline = interaction.baseline_input_tokens
    inferred = baseline is not None
    # The rate this saving is worth, frozen now so a later price change cannot
    # rewrite it -- but read from cache only, never resolved here. Detecting the
    # agent's model scans the local transcripts, which measures around six
    # seconds on a repository with no Codex history, and this runs inside the
    # agent's own tool call. Once a day is still far too often to stall one.
    #
    # So the tool call takes an unpriced event and `repowise distill` or
    # `repowise saved` fills the cache; the report counts priced and unpriced
    # tokens separately for exactly this reason.
    pricing = resolve_pricing_snapshot(interaction.repo_root, allow_scan=False)

    payload: dict[str, Any] = {
        "event_id": interaction.event_id,
        "idempotency_key": scoped_idempotency_key(
            str(interaction.repo_root), "mcp", interaction.event_id
        ),
        "occurred_at": _now(),
        "surface": "mcp",
        "integration": interaction.agent,
        "agent": interaction.agent,
        "session_id": interaction.session_id,
        "request_id": interaction.request_id,
        "operation": interaction.tool,
        # Inferred only when a counterfactual was actually derived. Truncation
        # on its own is a measured before/after of bytes that really moved.
        "evidence_kind": "inferred" if inferred else "measured",
        "estimator": _ESTIMATOR,
        "token_unit": _TOKEN_UNIT,
        "result_state": result_state,
        "is_usable": is_usable,
        "baseline_input_tokens": baseline,
        "pre_budget_input_tokens": interaction.pre_budget_input_tokens,
        "delivered_input_tokens": delivered,
        "omission_refs": _omission_refs(result),
        "metadata": interaction.identity_metadata or None,
        **(pricing.as_payload() if pricing else {}),
    }
    return recorder.record_event(interaction.repo_root, payload)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
