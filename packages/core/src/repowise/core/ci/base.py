"""Which branch a CI change targets, read from the CI's own variables.

CI checkouts are detached, rarely set ``origin/HEAD`` and have no local trunk
branch, so the usual "default branch" lookup finds nothing and a gate that
diffs ``HEAD...HEAD`` would pass having measured nothing. The pull-request
variables name the target directly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from ..forges.base import env_flag
from ..forges.ci import ci_systems, detect_ci, read_base_branch

#: CIs detected by presence alone: no forge, and no build facts read.
_PRESENCE_ONLY = ("BUILDKITE", "TEAMCITY_VERSION")

#: Variables whose presence marks an automated CI run. ``CI`` covers most hosts;
#: the rest catch the ones that do not set it. Telemetry reads the same list.
CI_ENV_VARS = (
    "CI",
    *(var for _, system in ci_systems() for var in system.markers),
    *_PRESENCE_ONLY,
)

#: Variables naming the branch a change will merge into, from every known CI.
#: A branch, not a base commit, so a pipeline that runs on a merge onto the
#: target's tip (merged results) still diffs only the change's own lines.
CI_BASE_VARS = tuple(var for _, system in ci_systems() for var in system.base_branch)


def in_ci(env: Mapping[str, str] | None = None) -> bool:
    """Whether this runs in a CI job: a CI marker or a pull-request variable is set."""
    env = os.environ if env is None else env
    return any(env_flag(env.get(var)) for var in (*CI_ENV_VARS, *CI_BASE_VARS))


def _ci_base_branch(env: Mapping[str, str]) -> str | None:
    """The detected CI's target branch, else any CI's: a job re-run by hand or a
    test may set the branch variable without the CI's marker."""
    if branch := detect_ci(env).base_branch:
        return branch
    return next(filter(None, (read_base_branch(s, env) for _, s in ci_systems())), None)


class BaseNotFoundError(ValueError):
    """No target branch could be determined; the caller should ask for a revspec."""


def default_revspec(repo_root: str, env: Mapping[str, str] | None = None) -> str:
    """``<base>...HEAD`` for the change being checked.

    The base comes from the CI's target branch (:data:`CI_BASE_VARS`), else
    the repository's default branch, else ``origin/main`` / ``origin/master``. Raises
    :class:`BaseNotFoundError` when none resolves.
    """
    from .. import git_refs

    env = os.environ if env is None else env
    if branch := _ci_base_branch(env):
        return f"origin/{branch}...HEAD"
    base = git_refs.default_base(repo_root)
    if base == "HEAD":
        base = next(
            (b for b in ("origin/main", "origin/master") if git_refs.resolve(repo_root, b)), ""
        )
    if not base:
        raise BaseNotFoundError(
            "Could not tell which branch this change targets. Pass REVSPEC, "
            "e.g. origin/main...HEAD."
        )
    return f"{base}...HEAD"
