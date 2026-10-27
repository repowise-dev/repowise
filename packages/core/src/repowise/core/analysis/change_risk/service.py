"""Shared live change-risk orchestration for CLI and MCP surfaces."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ...owned_paths import OWNED_PATTERNS
from ..git_cli import generated_worktree_paths
from ..risk_semantics import change_risk_authority, change_risk_scales
from .baseline import (
    BaselineSample,
    baseline_samples_cached,
    densities_excluding,
    resolve_anchor_sha,
    scores_excluding,
)
from .features import (
    GIT_TIMEOUT_SECONDS,
    ChangeFeatures,
    _git,
    extract_commit_features,
    extract_range_features,
    extract_worktree_features,
    split_revspec,
    working_tree_is_dirty,
)
from .fix_history import (
    FixHistoryUnavailableError,
    change_fix_density,
    fix_density_percentile,
    fix_pressure,
    hot_files,
)
from .model import SCORE_MEASURES, SCORE_UNIT, ChangeRisk, score_change
from .normalize import RiskNormalizer, review_priority_classification

_MIN_BASELINE = 8


@dataclass(frozen=True)
class ChangeRiskResult:
    """A live change score, its repo-relative ranking, and its fix-history load."""

    features: ChangeFeatures
    risk: ChangeRisk
    percentile: float | None
    priority: str | None
    baseline_sample_size: int
    riskignore_excludes: tuple[str, ...]
    request_excludes: tuple[str, ...]
    working_tree: bool = False  # scored the uncommitted change, not a commit
    # The part of ``riskignore_excludes`` that is repowise's own output rather
    # than the user's ``.riskignore``: not the change at all, so test selection
    # drops it too, while the user's filters leave selection whole.
    owned_excludes: tuple[str, ...] = ()
    # Bug-fix history of the ground this change stands on. ``density`` is the
    # churn-weighted mean fix pressure of the touched files, ``percentile`` ranks
    # it against the same measure over the repo's own recent commits, and
    # ``hot_files`` names where the pressure is. Unlike the score, none of
    # these grow with diff size.
    fix_density: float = 0.0
    fix_percentile: float | None = None
    hot_files: tuple[tuple[str, int, float], ...] = ()  # (path, churn, pressure)
    # False when the history walk could not run (git failure, timeout on a very
    # large repository). Distinguishes "no fixes here" from "we could not look",
    # which the surfaces would otherwise report identically.
    fix_history_available: bool = True
    # The ref the fix history was read at (see ``history_ref``), so the
    # line-scoped fix record can be read at the same point. ``None`` when the
    # change came from file stats with no checkout behind it.
    history_ref: str | None = None
    # The commit the baseline sample runs back from (the same ref as
    # ``history_ref``, resolved), so a kept rank can say what it ranked
    # against. ``None`` when no sample was walked.
    baseline_as_of: str | None = None


def riskignore_patterns(repo_path: str) -> tuple[str, ...]:
    """Non-comment patterns of the repository-root ``.riskignore``, then repowise's own paths.

    Repowise's output is not the user's change, so the paths it writes whole
    (:data:`~repowise.core.owned_paths.OWNED_PATTERNS`) are left out of every
    score, commits and ranges included, so a tracked, hand-edited Kiro steering
    file or workspace config is not scored either. Only those present are
    listed, so a repo that never ran repowise reports no exclusions it did not
    ask for.
    """
    proc = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return ()
    root = Path(proc.stdout.strip())
    owned = tuple(p for p in OWNED_PATTERNS if (root / p).exists())
    ignore_file = root / ".riskignore"
    if not ignore_file.is_file():
        return owned
    return (
        tuple(
            line
            for line in ignore_file.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        + owned
    )


def range_anchor(repo_path: str, base: str, head: str) -> str:
    """Anchor a range's baseline to where its two sides diverged.

    Keeps the range's own commits out of the distribution it is measured
    against, and lets ranges off the same fork point share one memoized walk.
    Note this also drops commits that landed on *base* after the fork: they are
    cohort members, but including them would mean re-walking per head.
    """
    merge_base = _git(["merge-base", base, head], repo_path, check=False).strip()
    return merge_base or base


def history_ref(repo_path: str, revspec: str | None, *, working_tree: bool = False) -> str:
    """The ref a change's fix history and baseline sample are read at, before the change.

    One anchor for every spelling, so a merge scored as ``sha`` and as
    ``sha^1..sha`` ranks against the same commits, wherever HEAD has moved
    since. Uncommitted work reads at ``HEAD``; a range at its merge-base, not at
    ``base``'s tip (with three dots the diff starts there, so base's later
    commits are not this change's ground); a commit at its parent, so it is
    never credited with fixes that only landed because of it. A root commit's
    parent does not resolve, which yields an empty record: the honest answer
    for the first commit in a repository.
    """
    if working_tree:
        return "HEAD"
    target = revspec or "HEAD"
    parts = split_revspec(target)
    if parts is not None:
        base, _, head = parts
        return range_anchor(repo_path, base, head)
    return f"{target}^"


def normalize_extensions(extensions: tuple[str, ...]) -> tuple[str, ...]:
    """Add a leading dot to requested suffixes, matching the CLI contract."""
    return tuple(ext if ext.startswith(".") else f".{ext}" for ext in extensions)


def score_live_change(
    repo_path: str,
    revspec: str | None = None,
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
    include_paths: tuple[str, ...] = (),
    baseline: int = 200,
) -> ChangeRiskResult:
    """Score a commit or ``base..head`` range with optional live filters.

    With no *revspec* the subject is "the change in front of me": the
    uncommitted work if the tree is dirty, otherwise ``HEAD``. A caller that
    just wrote code and asked for a score means the code it wrote, and the
    previous commit is the one answer that is certainly wrong. An explicit
    *revspec* — ``"HEAD"`` included — always means committed refs.
    """
    if baseline < 0:
        raise ValueError("baseline must be non-negative")
    # A ref reaches git as an argument; one that reads as an option (--output=f)
    # would run as a git flag instead of being looked up as a revision.
    if revspec and any(p.startswith("-") for p in (split_revspec(revspec) or (revspec,))):
        raise ValueError(f"Invalid revspec {revspec!r}: a ref cannot start with '-'.")

    extensions = normalize_extensions(extensions)
    from_riskignore = riskignore_patterns(repo_path)
    # The baseline walks committed history, which the working-tree-only
    # exclusions below say nothing about; keeping them out keeps its cache key.
    baseline_excludes = from_riskignore + exclude_patterns
    target = revspec or "HEAD"
    uncommitted = None
    generated: tuple[str, ...] = ()
    if revspec is None and working_tree_is_dirty(repo_path):
        # A managed file (CLAUDE.md, .mcp.json) whose edit is only repowise's
        # block or entry is repowise's output too, while the user's edits count.
        generated = generated_worktree_paths(repo_path)
        worktree_ignores = from_riskignore + generated
        uncommitted = extract_worktree_features(
            repo_path,
            extensions=extensions,
            exclude_patterns=worktree_ignores + exclude_patterns,
            include_paths=include_paths,
        )
    # A tree dirty only in paths the filters drop is not a change this command
    # can score, so fall through to HEAD rather than answer "empty change".
    working_tree = uncommitted is not None and uncommitted.nf > 0
    if working_tree:
        from_riskignore = worktree_ignores
    effective_excludes = from_riskignore + exclude_patterns
    # Ref the fix record and the baseline sample are read from (see ``history_ref``).
    history = history_ref(repo_path, target, working_tree=working_tree)
    if working_tree:
        features = uncommitted
    elif (parts := split_revspec(target)) is not None:
        base, sep, head = parts
        features = extract_range_features(
            repo_path,
            base,
            head,
            extensions=extensions,
            exclude_patterns=effective_excludes,
            include_paths=include_paths,
            sep=sep,
        )
    else:
        features = extract_commit_features(
            repo_path,
            target,
            extensions=extensions,
            exclude_patterns=effective_excludes,
            include_paths=include_paths,
        )

    try:
        pressure: dict[str, float] | None = fix_pressure(repo_path, history)
    except FixHistoryUnavailableError:
        pressure = None

    samples: list[BaselineSample] = []
    as_of = None
    if baseline:
        as_of = resolve_anchor_sha(repo_path, history)
        samples = baseline_samples_cached(
            repo_path,
            history,
            baseline,
            extensions,
            exclude_patterns=baseline_excludes,
            include_paths=include_paths,
        )

    result = assess_change(
        features,
        fix_pressure=pressure,
        # The sample ends before the change, so there is no self to exclude.
        baseline_scores=scores_excluding(samples, ""),
        baseline_fix_densities=densities_excluding(samples, "", pressure or {}),
        working_tree=working_tree,
        riskignore_excludes=from_riskignore,
        request_excludes=exclude_patterns,
    )
    owned = tuple(p for p in from_riskignore if p in OWNED_PATTERNS)
    owned += generated if working_tree else ()
    return replace(result, history_ref=history, owned_excludes=owned, baseline_as_of=as_of)


def assess_change(
    features: ChangeFeatures,
    *,
    fix_pressure: Mapping[str, float] | None = None,
    baseline_scores: Sequence[float] = (),
    baseline_fix_densities: Sequence[float] = (),
    min_baseline: int = _MIN_BASELINE,
    working_tree: bool = False,
    riskignore_excludes: tuple[str, ...] = (),
    request_excludes: tuple[str, ...] = (),
) -> ChangeRiskResult:
    """Score an already-extracted change. The whole risk composition, no IO.

    This is the pure tail of :func:`score_live_change`: model scoring, the
    fix-history load the change stands on, and the repo-relative ranking.
    Everything upstream of it -- resolving a revspec, walking git for features,
    walking git for fix pressure, sampling a baseline -- is IO, and stays with
    the caller. A consumer holding file stats from an API rather than a
    checkout reaches this directly and gets the same numbers, because they come
    from the same composition rather than a second copy of it.

    ``fix_pressure`` of ``None`` means the history walk could not run, which is
    reported as ``fix_history_available=False``. An empty mapping is different:
    the walk ran and found no fixes.

    ``baseline_scores`` ranks this change against its cohort. Fewer than
    *min_baseline* of them leaves ``percentile`` and ``priority`` as ``None`` --
    an honest "no cohort to rank against" rather than a percentile computed from
    too little to mean anything.
    """
    risk = score_change(features)
    pressure = dict(fix_pressure or {})
    density = change_fix_density(pressure, features.file_churn)

    percentile: float | None = None
    priority: str | None = None
    if len(baseline_scores) >= min_baseline:
        normalizer = RiskNormalizer.from_scores(list(baseline_scores))
        # Author experience is a property of the author, not of the change, so
        # it is excluded from the score this change is *ranked* by.
        rank_score = score_change(replace(features, exp=None)).score
        percentile = normalizer.percentile(rank_score)
        priority = normalizer.priority(rank_score)

    return ChangeRiskResult(
        features=features,
        risk=risk,
        percentile=percentile,
        priority=priority,
        baseline_sample_size=len(baseline_scores),
        riskignore_excludes=riskignore_excludes,
        request_excludes=request_excludes,
        working_tree=working_tree,
        fix_density=round(density, 3),
        fix_percentile=fix_density_percentile(list(baseline_fix_densities), density),
        hot_files=hot_files(pressure, features.file_churn),
        fix_history_available=fix_pressure is not None,
    )


def change_risk_payload(result: ChangeRiskResult, *, scales: bool = False) -> dict:
    """Render the machine-readable response shared by the CLI and MCP tool.

    ``fix_history`` leads: it is the block that distinguishes a surgical edit to
    a file that keeps breaking from a bulk rename of files that never have.
    ``score`` and ``risk_percentile`` describe the *shape* of the diff and are
    kept for continuity, labelled for what they measure — see ``score_measures``.
    ``fallback_band`` is the absolute model-score band, non-null only when there
    was no baseline to rank against. ``score_unit`` names the unit that band
    assumes.

    ``risk_authority`` always ships: it names the field to act on. The
    per-field ``risk_scales`` dictionary is identical on every call, so it
    ships only when ``scales`` is set.
    """
    features, risk = result.features, result.risk
    return {
        "ref": features.ref,
        "working_tree": result.working_tree,
        "fix_history": {
            "available": result.fix_history_available,
            "density": result.fix_density,
            "percentile": result.fix_percentile,
            "files": [
                {"path": path, "churn": churn, "fix_pressure": pressure}
                for path, churn, pressure in result.hot_files
            ],
        },
        "risk_authority": change_risk_authority(),
        "score": risk.score,
        "score_measures": SCORE_MEASURES,
        "score_unit": SCORE_UNIT,
        "risk_percentile": round(result.percentile, 1) if result.percentile is not None else None,
        "review_priority": result.priority,
        "classification": review_priority_classification(result.priority),
        "fallback_band": risk.level if result.priority is None else None,
        "baseline_sample_size": result.baseline_sample_size,
        "exclude_patterns": list(result.riskignore_excludes + result.request_excludes),
        **({"risk_scales": change_risk_scales()} if scales else {}),
        "is_fix": features.is_fix,
        "features": {
            "la": features.la,
            "ld": features.ld,
            "nf": features.nf,
            "nd": features.nd,
            "ns": features.ns,
            "entropy": round(features.entropy, 4),
            "exp": features.exp,
        },
        "drivers": [
            {
                "feature": driver.feature,
                "value": driver.value,
                "contribution": round(driver.contribution, 4),
                "label": driver.label,
            }
            for driver in risk.top_drivers
        ],
    }
