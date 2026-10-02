"""Which branch a CI change targets, read from the CI's own variables.

CI checkouts are detached, rarely set ``origin/HEAD`` and have no local trunk
branch, so the usual "default branch" lookup finds nothing and a gate that
diffs ``HEAD...HEAD`` would pass having measured nothing. The pull-request
variables name the target directly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from .. import git_refs

#: Variables naming the branch a change will merge into, in the order they are
#: consulted; each is read from the ``origin`` remote. A branch, not a base
#: commit, so a pipeline that runs on a merge onto the target's tip (merged
#: results) still diffs only the change's own lines.
CI_BASE_VARS = (
    "GITHUB_BASE_REF",  # GitHub Actions pull_request
    "CI_MERGE_REQUEST_TARGET_BRANCH_NAME",  # GitLab merge request
    "CHANGE_TARGET",  # Jenkins multibranch pull request
    "BITBUCKET_PR_DESTINATION_BRANCH",  # Bitbucket Pipelines
)


class BaseNotFoundError(ValueError):
    """No target branch could be determined; the caller should ask for a revspec."""


def default_revspec(repo_root: str, env: Mapping[str, str] | None = None) -> str:
    """``<base>...HEAD`` for the change being checked.

    The base comes from :data:`CI_BASE_VARS`, else the repository's default
    branch, else ``origin/main`` / ``origin/master``. Raises
    :class:`BaseNotFoundError` when none resolves.
    """
    env = os.environ if env is None else env
    for var in CI_BASE_VARS:
        if branch := env.get(var, "").strip():
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
