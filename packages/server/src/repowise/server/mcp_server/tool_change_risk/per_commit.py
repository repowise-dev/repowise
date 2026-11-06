"""``per_commit``: one compact row per commit of a range, in one call."""

from __future__ import annotations

import time
from typing import Any

from repowise.core.analysis.change_risk.features import split_revspec

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._helpers import attach_ignored_arguments, resolve_enum_argument
from repowise.server.mcp_server._savings import declare_replaced
from repowise.server.mcp_server._savings.counterfactual import change_risk_replaced_tokens

from .health import _repository

#: Commits one call scores, oldest first; the reply names the call for the rest.
PER_COMMIT_LIMIT = 30

#: Drivers named per row: the ones that raised the score most.
_ROW_DRIVERS = 2


def _commit_row(result: Any, diagnostics: bool) -> dict[str, Any]:
    """One commit: what it is, its size, its rank and why it ranks there."""
    feats = result.features
    row: dict[str, Any] = {
        "sha": feats.ref[:12],
        "subject": feats.subject,
        "files": feats.nf,
        "lines": f"+{feats.la} -{feats.ld}",
    }
    if feats.nf == 0:
        return row
    if result.percentile is not None:
        row["risk_percentile"] = round(result.percentile, 1)
    else:
        row["fallback_band"] = result.risk.level
    raised = [d for d in result.risk.top_drivers if d.value is not None and d.contribution > 0]
    if raised:
        row["drivers"] = [d.label for d in raised[:_ROW_DRIVERS]]
    if result.hot_files:
        row["fix_prone"] = [path for path, _churn, _pressure in result.hot_files[:2]]
    if feats.is_fix:
        row["is_fix"] = True
    if diagnostics:
        row["score"] = result.risk.score
    return row


def _rank(result: Any) -> tuple[float, float]:
    """Sort key for the commit most worth a full review."""
    return (result.percentile if result.percentile is not None else -1.0, result.risk.score)


def _ranked_against(scored: Any) -> str | None:
    """Which history the percentiles rank against, or None when nothing was sampled."""
    sample = scored.results[0].baseline_sample_size if scored.results else 0
    if not sample or not scored.baseline_as_of:
        return None
    return f"{sample} commits before {scored.baseline_as_of[:12]}, shared by every row"


def _shape(scored: Any, revspec: str, diagnostics: bool) -> dict[str, Any]:
    """The reply body: rows, then the calls that go deeper or further."""
    payload: dict[str, Any] = {
        "ref": revspec,
        "commits": [_commit_row(r, diagnostics) for r in scored.results],
    }
    if ranked := _ranked_against(scored):
        payload["ranked_against"] = ranked
    counted = [r for r in scored.results if r.features.nf]
    if counted:
        top = max(counted, key=_rank).features.ref[:12]
        payload["review"] = (
            f"get_change_risk(revspec='{top}') reviews the top-ranked commit in full; "
            f"get_change_risk(revspec='{revspec}') reviews the range as one change."
        )
    if scored.merges:
        payload["merges_skipped"] = scored.merges
    remaining = scored.total - len(scored.results)
    if remaining > 0:
        _base, _sep, head = split_revspec(revspec) or ("", "", "HEAD")
        last = scored.results[-1].features.ref[:12]
        payload["more"] = {
            "commits": remaining,
            "call": f"get_change_risk(revspec='{last}..{head}', per_commit=True)",
        }
    return payload


def _refusal(revspec: str | None, finding_id: str | None) -> dict[str, Any] | None:
    """The error for a request per_commit cannot answer, else None."""
    if finding_id is not None:
        return {"error": "finding_id expands a finding of one change; call without per_commit."}
    if not revspec or split_revspec(revspec) is None:
        return {
            "error": (
                f"per_commit needs a base..head range, got {revspec or 'no revspec'!r}. "
                "Example: get_change_risk(revspec='main..HEAD', per_commit=True)."
            )
        }
    return None


async def per_commit_reply(
    ctx: Any,
    revspec: str | None,
    extensions: list[str] | None,
    exclude_patterns: list[str] | None,
    includes: tuple[str, ...],
    baseline: int,
    include: list[str] | None,
    finding_id: str | None,
    started: float,
) -> dict[str, Any]:
    """Score each commit of *revspec* on its own and return one row per commit."""
    if refusal := _refusal(revspec, finding_id):
        return refusal
    ignored: list[dict] = []
    for block in include or ():
        resolve_enum_argument(block, ("diagnostics",), argument="include", ignored=ignored)
    scored, error = await _pkg._score_guarded(
        revspec,
        _pkg.score_range_commits,
        str(ctx.path),
        revspec,
        extensions=tuple(extensions or ()),
        exclude_patterns=tuple(exclude_patterns or ()),
        include_paths=includes,
        baseline=baseline,
        limit=PER_COMMIT_LIMIT,
    )
    if error is not None:
        return error
    payload = _shape(scored, revspec, "diagnostics" in (include or ()))
    touched = sorted({path for r in scored.results for path, _churn in r.features.file_churn})
    payload["_meta"] = _pkg._build_meta(
        timing_ms=(time.perf_counter() - started) * 1000,
        repository=await _repository(ctx),
        targets=touched or None,
        extra={"source": "live_git"},
    )
    declare_replaced(
        payload,
        sum(change_risk_replaced_tokens(r.features.nf) for r in scored.results),
    )
    attach_ignored_arguments(payload, ignored)
    return payload
