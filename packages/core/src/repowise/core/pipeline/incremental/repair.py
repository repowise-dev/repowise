"""The repair marker: a commit range an update failed to fully persist.

Shared by ``repowise update`` and the workspace update, which write the same
``.repowise/state.json``. Either one honours a marker the other left.
"""

from __future__ import annotations

from typing import Any

from repowise.core.git_sha import is_sha_prefix

# A repair window that has grown past this many commits stopped being a repair
# and became a re-index: the diff it forces every update to walk is no longer
# change-sized. Bounding it is what stops a step that fails on every run from
# pinning the window open forever.
_REPAIR_MAX_COMMITS = 500


def record_repair_marker(new_state: dict, prior_state: dict, failed_steps: list[str]) -> None:
    """Record, or clear, the commit range this update failed to fully persist.

    ``last_sync_commit`` advances whether or not the persist steps succeeded, so
    a failed step used to take its commit range with it: nothing ever revisited
    those commits, and their git metadata, health rows, symbols or edges were
    skipped permanently and announced exactly once in the completion panel. The
    marker keeps the *old* pointer so the next update diffs from there and
    re-covers the range, while the pointer itself stays current for every other
    reader of the state file.

    Carrying the oldest unrepaired commit forward rather than the newest is the
    part that makes it hold: three failed runs in a row still have to re-cover
    all three ranges, not just the last one.

    Only range-scoped failures land here (see ``persist_incremental_index``),
    so a repo-wide step that fails on every single run cannot keep the marker
    alive: it heals itself on the next update instead.
    """
    if not failed_steps:
        new_state.pop("pending_repair", None)
        return
    prior = prior_state.get("pending_repair")
    if not isinstance(prior, dict):  # a hand-edited or truncated state file
        prior = {}
    from_commit = prior.get("from_commit") or prior_state.get("last_sync_commit")
    if not from_commit:
        # Nothing to diff back to (a first index, or a state file with no
        # pointer). Widening from an unknown base is not something we can do
        # safely, so leave no marker rather than a broken one.
        new_state.pop("pending_repair", None)
        return
    # Union, not replace: the marker names one range, so it has to name
    # everything still unrepaired in it. Run 1 failing the git persist and run 2
    # failing only the health persist leaves both unrepaired over the same span.
    prior_steps = prior.get("steps")
    prior_steps = prior_steps if isinstance(prior_steps, list) else []
    new_state["pending_repair"] = {
        "from_commit": from_commit,
        "steps": sorted({*prior_steps, *failed_steps}),
    }


def resolve_repair_base(
    repo_path: Any, state: dict, base_ref: str, head: str | None
) -> tuple[str, str | None]:
    """Widen *base_ref* back to an unrepaired range, or report giving up on it.

    Returns ``(base_ref, give_up_reason)``. The reason is non-None exactly when
    the marker should be dropped without being honoured, and it is written for
    the user rather than the log.

    Three things have to hold before the marker is honoured, and each one closes
    a way this could go wrong:

    * the recorded commit still resolves and is an *ancestor* of the current
      base, so honouring it can only ever move the diff backwards. Without the
      ancestry check a rebase, a branch switch or a docs-mode base that is
      already older would let the marker move the base *forward* and skip the
      very commits it exists to re-cover;
    * the range is still bounded (see ``_REPAIR_MAX_COMMITS``);
    * it differs from the base we already have, so a marker that has effectively
      caught up costs nothing.
    """
    import subprocess

    marker = state.get("pending_repair")
    from_commit = marker.get("from_commit") if isinstance(marker, dict) else None
    if not from_commit or from_commit == base_ref:
        return base_ref, None
    # State-file values reach a git argument list, where a leading "-" is an option.
    refs = (from_commit, base_ref)
    if not all(isinstance(ref, str) and is_sha_prefix(ref.lower()) for ref in refs):
        return base_ref, None

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(repo_path), *args], capture_output=True, timeout=30, text=True
        )

    try:
        if (
            _git(
                "merge-base", "--is-ancestor", "--end-of-options", from_commit, base_ref
            ).returncode
            != 0
        ):
            # Non-zero covers both "not an ancestor" (rebased, force-pushed,
            # branch switched) and "cannot resolve that object" (gc'd, or a
            # shallow clone that never fetched it), and git does not
            # distinguish them by exit code. Say both rather than assert one.
            return base_ref, (
                f"the recorded commit {from_commit[:8]} is not an ancestor of this "
                "branch's history, or is not present in this clone"
            )
        counted = _git(
            "rev-list", "--count", "--end-of-options", f"{from_commit}..{head or 'HEAD'}"
        )
        if counted.returncode == 0 and int(counted.stdout.strip() or 0) > _REPAIR_MAX_COMMITS:
            return base_ref, (
                f"the range has grown past {_REPAIR_MAX_COMMITS} commits, which is "
                "a re-index rather than a repair"
            )
    except Exception:
        # git being unavailable is not a reason to widen blindly.
        return base_ref, None

    return from_commit, None


def unchecked_repair_marker(state: dict, base_ref: str, give_up: str | None) -> dict | None:
    """The marker a run must keep because it could not re-cover the marker's range.

    Asked after ``resolve_repair_base`` returned *base_ref* and *give_up*. No
    give-up and no widening means git could not check the marker, so the run
    diffed from its own base and the range is still unrepaired. A marker that
    caught up, was given up on, or holds no commit id is not kept.
    """
    marker = state.get("pending_repair")
    repair_from = marker.get("from_commit") if isinstance(marker, dict) else None
    if (
        give_up is None
        and isinstance(repair_from, str)
        and repair_from != base_ref
        and is_sha_prefix(repair_from.lower())
    ):
        return marker
    return None
