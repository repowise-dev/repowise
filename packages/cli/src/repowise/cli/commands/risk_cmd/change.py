"""Resolving and scoring the change a REVSPEC names."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from repowise.cli.ci import CannotEvaluateError, cannot_evaluate, ci_notices, ci_revspec, repo_root
from repowise.core.analysis.change_risk import ChangeRiskResult, score_live_change


@dataclass(frozen=True)
class _Subject:
    """The change a REVSPEC run scores, and how it reports it."""

    repo_path: str
    revspec: str | None
    fmt: str
    fail_above: float | None


def _resolve_subject(
    repo_path: str, revspec: str | None, fmt: str, fail_above: float | None
) -> _Subject:
    """The change to score: *revspec*, else the CI change for a gate or CI format."""
    if revspec is None and (fail_above is not None or fmt in ("markdown", "github")):
        # A CI checkout of a pull request is a merge commit, so HEAD alone is
        # the wrong subject: the change is what the branch did since the target.
        try:
            revspec = ci_revspec(str(repo_root(repo_path)), None)
        except CannotEvaluateError as exc:
            cannot_evaluate(fmt, exc.code, str(exc))
    return _Subject(repo_path, revspec, fmt, fail_above)


def _score(
    subject: _Subject, extensions: tuple[str, ...], exclude: tuple[str, ...], baseline: int
) -> ChangeRiskResult:
    """Score the change, or exit 2 when it cannot be read or, gated, ranked."""
    status = ci_notices(subject.fmt)
    _verify_revision(subject)
    if baseline and subject.fmt == "table":
        status.print(f"[dim]Sampling up to {baseline} recent commits…[/dim]")
    try:
        result = score_live_change(
            subject.repo_path,
            subject.revspec,
            extensions=extensions,
            exclude_patterns=exclude,
            baseline=baseline,
        )
    except Exception as exc:
        # Git errors (bad revspec, not a repo, missing history) cannot be scored.
        cannot_evaluate(
            subject.fmt,
            "change_unreadable",
            f"Could not read change {subject.revspec or 'HEAD'!r} in {subject.repo_path}: "
            f"{_git_error_detail(exc)}",
        )
    if subject.fail_above is not None and result.percentile is None:
        from repowise.core.analysis.change_risk.render import unranked_reason

        cannot_evaluate(subject.fmt, "no_baseline", unranked_reason(result, baseline))
    if result.features.nf == 0:
        status.print(
            f"[yellow]No counted file changes in {result.features.ref!r} "
            f"(check the revspec, --ext, or exclusion filters).[/yellow]"
        )
    return result


def _git_error_detail(exc: Exception) -> str:
    """Git's own message for a failed git call, else the exception text."""
    stderr = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else None
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return lines[0] if lines else str(exc)


def _verify_revision(subject: _Subject) -> None:
    """Exit 2 with git's message when a single-commit revspec does not resolve.

    Checked before the sampling notice so an unknown ref is reported first and
    alone. Ranges and the working tree are left to the scorer.
    """
    revspec = subject.revspec
    if not revspec or ".." in revspec:
        return
    try:
        commit = f"{revspec}^{{commit}}"
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", commit],
            cwd=subject.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        # --quiet suppresses the message; ask git again for the human one.
        probe = subprocess.run(
            ["git", "rev-parse", "--end-of-options", revspec],
            cwd=subject.repo_path,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            detail = f"{revspec!r} does not name a commit"
        elif "unknown revision" in probe.stderr:
            detail = _git_error_detail(
                subprocess.CalledProcessError(probe.returncode, probe.args, stderr=probe.stderr)
            )
        else:
            # Not a repository, or some other git failure: the scorer reports it.
            return
        cannot_evaluate(
            subject.fmt,
            "change_unreadable",
            f"Could not read change {revspec!r} in {subject.repo_path}: {detail}",
        )
    except OSError:
        return
