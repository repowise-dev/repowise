"""Turning a model's JSON answer into decisions, and counting batches that fail."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Any

import structlog

from .records import DecisionSourceError, EmptyModelResponseError, ExtractedDecision

logger = structlog.get_logger(__name__)


def _coerce_line(value: object) -> int | None:
    """Read an LLM-reported line number, or ``None`` if it isn't one.

    Models answer ``12``, ``"12"`` and ``"line 12"`` interchangeably; anything
    that isn't a positive integer is no attribution at all and must not be
    guessed at.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        digits = re.search(r"\d+", value)
        if digits:
            line = int(digits.group())
            return line if line > 0 else None
    return None


def _coerce_paths(value: object) -> list[str] | None:
    """A list of path-ish strings out of whatever the model returned.

    ``None`` for an absent *or malformed* key, so that a model which answered
    ``[]`` is told apart from one that never usefully answered: the first is a
    decision about none of the commit's files, which binds the record to
    nothing, and the second is a provider that has not seen the new prompt,
    which falls back to the commit list. A list of dicts or numbers is the
    second case, not the first -- reading it as "chose nothing" would silently
    make the record govern nothing forever on a shape error.

    Models return a bare string for a one-element list often enough to be
    worth handling.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    kept = [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if value and not kept:
        return None
    return kept


def _collect_batches(
    source: str,
    results: list[Any],
) -> list[ExtractedDecision]:
    """Flatten ``asyncio.gather(..., return_exceptions=True)`` output.

    Exceptions are logged per batch. If nothing survived and there was work
    to do, the source failed outright and says so instead of returning a
    zero that reads like an empty repository.
    """
    decisions: list[ExtractedDecision] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, list):
            decisions.extend(result)
        else:
            errors.append(f"{type(result).__name__}: {result}")
            logger.warning(
                "decision_extractor.batch_failed",
                source=source,
                error=str(result),
            )
    if errors and len(errors) == len(results):
        raise DecisionSourceError(f"all {len(errors)} batch(es) failed — {errors[0]}")
    return decisions


async def _run_batches(
    source: str,
    items: Sequence[Any],
    size: int,
    process: Any,
) -> list[ExtractedDecision]:
    """Run *process* over *items* in batches of *size*, concurrently.

    Each batch's failure is contained and counted by :func:`_collect_batches`,
    which raises only when every batch failed.
    """
    batches = [items[i : i + size] for i in range(0, len(items), size)]
    results = await asyncio.gather(*[process(b) for b in batches], return_exceptions=True)
    return _collect_batches(source, list(results))


def _load_json_payload(content: str) -> Any | None:
    """Decode a model's JSON answer, tolerating code fences and surrounding prose."""
    if content.startswith("```"):
        # Remove markdown code fences
        lines = content.split("\n")
        content = "\n".join(line for line in lines if not line.strip().startswith("```"))
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return _embedded_json_array(content)


def _embedded_json_array(content: str) -> Any | None:
    """The outermost ``[...]`` span inside surrounding prose, decoded, else None."""
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _decision_from_item(item: dict) -> ExtractedDecision:
    """One decision from one object of a model's JSON answer."""
    return ExtractedDecision(
        title=item.get("title", ""),
        context=item.get("context", ""),
        decision=item.get("decision", ""),
        rationale=item.get("rationale", ""),
        alternatives=item.get("alternatives", []),
        consequences=item.get("consequences", []),
        tags=item.get("tags", []),
        # Only the two commit prompts ask for this. Every other prompt omits
        # the key, so this stays None and the miner that owns those decisions
        # keeps scoping them its own way.
        proposed_files=_coerce_paths(item.get("affected_files")),
        evidence_commits=[item["commit_sha"]] if "commit_sha" in item else [],
        # Which marker this came from, for the inline-marker miner's per-marker
        # attribution. Absent (and left None) for every other prompt; they
        # scope by sha or by file instead.
        evidence_line=_coerce_line(item.get("marker_line")),
        source_quote=item.get("source_quote", ""),
    )


def parse_decisions_json(content: str) -> list[ExtractedDecision]:
    """Parse LLM response as JSON array of decisions.

    A blank body raises :class:`EmptyModelResponseError`; every caller
    sits inside a gather or a fallback that counts that as a lost batch.
    """
    # Extract JSON from response (may be wrapped in markdown code blocks)
    content = content.strip()
    if not content:
        raise EmptyModelResponseError(
            "the model returned no content for this batch"
        )
    data = _load_json_payload(content)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [
        _decision_from_item(item)
        for item in data
        if isinstance(item, dict) and item.get("title", "")
    ]
