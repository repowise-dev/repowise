"""One change's health check: the comparison and the diff-risk rules, judged by the gate.

What ``repowise health check <revspec>`` runs, as a function the CLI and the
server share. Both sides are read as blobs (or from the working tree) through a
``RevisionSource``, so no index is needed. Locally that source is git; a host
with no checkout passes its own (``MappingRevisionSource`` over fetched blobs,
or one that reads a forge's API).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repowise.core.analysis.change_health.models import ChangeHealthDelta
    from repowise.core.analysis.change_health.sources import RevisionSource
    from repowise.core.analysis.health.config import HealthConfig
    from repowise.core.analysis.health_gate import GateResult


class HealthCheckUnavailable(ValueError):  # noqa: N818 - a reason, not a crash
    """The change could not be judged; ``code`` names why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HealthCheck:
    result: GateResult
    delta: ChangeHealthDelta
    #: The revspec judged, or ``"working tree"``.
    label: str

    def as_json(self) -> dict[str, Any]:
        from repowise.core.analysis.health_gate import as_json

        return as_json(self.result, self.delta, label=self.label)


def base_rules(source: RevisionSource, base: str) -> HealthConfig:
    """``.repowise/health-rules.json`` at the change's *base*; defaults when absent.

    Read at the base so a change cannot loosen its own gate.
    """
    from repowise.core.analysis.health.config import HEALTH_RULES_PATH, HealthConfig

    raw = source.read(base, [HEALTH_RULES_PATH]).get(HEALTH_RULES_PATH)
    if raw is None:
        return HealthConfig()
    try:
        return HealthConfig.from_json(raw.decode("utf-8"))
    except ValueError as exc:
        raise HealthCheckUnavailable(
            "rules_invalid",
            f"{HEALTH_RULES_PATH} at the change's base ({base[:12]}) is invalid: {exc}",
        ) from exc


def run_health_check(
    root: str | None,
    revspec: str | None,
    *,
    source: RevisionSource | None = None,
    fail_on: str = "introduced",
    fail_at: str = "high",
    levels: Mapping[str, str] | None = None,
) -> HealthCheck:
    """Judge *revspec* (``None``: the working tree against ``HEAD``) at the gate.

    The defaults are the CLI's: fail on introduced findings at high or above.
    Raises :class:`HealthCheckUnavailable` when the change cannot be read or
    judged.

    *source* reads both sides; omitted, it is git over the checkout at *root*.
    With an injected source *root* may be ``None``: nothing then scans a
    checkout or writes a cache under it, and *revspec* means whatever the
    source's ``resolve`` makes of it.
    """
    from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest
    from repowise.core.analysis.change_health.sources import GitRevisionSource
    from repowise.core.analysis.diff_risk import diff_risk_for_change
    from repowise.core.analysis.health_gate import NotEvaluatedError, diff_risk_findings, evaluate

    injected = source is not None
    if source is None:
        if root is None:
            raise ValueError("run_health_check needs a checkout root or a RevisionSource")
        source = GitRevisionSource(root)
    try:
        pair = source.resolve(revspec)
    except ValueError as exc:
        raise HealthCheckUnavailable("unsupported_range", str(exc)) from exc
    rules = base_rules(source, pair.base_sha)
    # An injected source has no checkout behind it: no package-root scan, no on-disk cache.
    service = ChangeHealthDeltaService(
        source,
        repo_path=None if injected else root,
        health_config=rules,
        cache_dir=False if injected else None,
    )
    label = revspec or "working tree"
    # One resolution serves the comparison and the diff-risk rules.
    delta = service.compare(DeltaRequest(root, revspec), pair=pair)
    risks = diff_risk_findings(diff_risk_for_change(source, pair, severities=dict(levels or {})))
    try:
        result = evaluate(delta, fail_on=fail_on, fail_at=fail_at, extra=risks)
    except NotEvaluatedError as exc:
        raise HealthCheckUnavailable(exc.code, str(exc)) from exc
    return HealthCheck(result, delta, label)
