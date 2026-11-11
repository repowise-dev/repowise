"""Checkouts the server owns: fetch the tracked branch before an index job.

A developer's working tree is never fetched or moved. A checkout opted in with
``managed_checkout: true`` in its own ``.repowise/config.yaml`` (``repowise
config set managed_checkout true``) is a clone the server keeps at its
remote: before each index job it fetches the
checked-out branch's upstream and fast-forwards to it, so a push webhook or
the poller indexes the commit that was pushed.

A managed checkout holds no local work. If it does (edited tracked files, or
commits the remote does not have) the job is refused with that reason and the
checkout and index are left as they were; nothing is reset or discarded.
Credentials are whatever the checkout's git config already uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import structlog

from repowise.core.redaction import redact_secrets
from repowise.core.repo_config import RepoConfigError, load_repo_config
from repowise.core.settings import resolve

logger = structlog.get_logger(__name__)

# A first fetch of a large monorepo can take minutes; an incremental one is fast.
_FETCH_TIMEOUT_S = 600
_GIT_TIMEOUT_S = 30


class ManagedCheckoutError(RuntimeError):
    """The managed checkout could not be brought to its remote head."""


def is_managed_checkout(repo_path: Path | str) -> bool:
    """Whether the server owns *repo_path* and may fetch and move it."""
    try:
        config = load_repo_config(repo_path)
    except RepoConfigError:
        # A broken file is not an opt-in.
        logger.warning("managed_checkout_config_unreadable", repo_path=str(repo_path))
        config = {}
    return bool(resolve("managed_checkout", config).value)


def _git(repo_path: Path | str, *args: str, timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    # Never wait on a credential prompt: the server has no one to answer it.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ManagedCheckoutError(f"git {args[0]} did not finish within {timeout}s") from None
    except OSError as exc:
        raise ManagedCheckoutError(f"could not run git: {exc}") from None


def _reason(result: subprocess.CompletedProcess) -> str:
    """The last line git wrote to stderr, with any credential blanked."""
    lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
    return redact_secrets(lines[-1].strip()) if lines else f"git exited {result.returncode}"


def _upstream(repo_path: Path | str) -> tuple[str, str, str]:
    """``(branch, remote, merge_ref)`` the checkout tracks, as ``git clone`` sets them."""
    head = _git(repo_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if head.returncode != 0:
        raise ManagedCheckoutError("HEAD is detached; check out the branch to track")
    branch = head.stdout.strip()
    remote = _git(repo_path, "config", "--get", f"branch.{branch}.remote").stdout.strip()
    merge = _git(repo_path, "config", "--get", f"branch.{branch}.merge").stdout.strip()
    if not remote or not merge:
        raise ManagedCheckoutError(f"branch {branch} has no upstream to fetch")
    return branch, remote, merge


def remote_head(repo_path: Path | str) -> str:
    """The commit the tracked branch points at on its remote, read without fetching."""
    _branch, remote, merge = _upstream(repo_path)
    result = _git(repo_path, "ls-remote", remote, merge, timeout=_FETCH_TIMEOUT_S)
    if result.returncode != 0:
        raise ManagedCheckoutError(f"could not read {remote}: {_reason(result)}")
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == merge:
            return sha.strip()
    raise ManagedCheckoutError(f"{remote} has no {merge}")


def fast_forward(repo_path: Path | str) -> tuple[str, str]:
    """Fetch the tracked branch and fast-forward the checkout to it.

    Returns ``(before, after)`` HEAD commits. Raises
    :class:`ManagedCheckoutError` with the reason when the fetch fails or the
    checkout holds local work; the checkout is then unchanged.
    """
    branch, remote, merge = _upstream(repo_path)
    before = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    # Untracked files are not local work here (.repowise/ itself is one); a
    # move that would overwrite one fails in the merge below instead.
    dirty = _git(repo_path, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0:
        raise ManagedCheckoutError(f"could not read the checkout's status: {_reason(dirty)}")
    if dirty.stdout.strip():
        raise ManagedCheckoutError(
            "the checkout has edited tracked files; a managed checkout must hold "
            "no local work, so it was not moved"
        )
    fetched = _git(repo_path, "fetch", "--quiet", remote, merge, timeout=_FETCH_TIMEOUT_S)
    if fetched.returncode != 0:
        raise ManagedCheckoutError(f"could not fetch {remote}: {_reason(fetched)}")
    if _git(repo_path, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD").returncode != 0:
        raise ManagedCheckoutError(
            f"branch {branch} has commits {remote} does not; a managed checkout "
            "must hold no local work, so it was not moved"
        )
    merged = _git(repo_path, "merge", "--ff-only", "--quiet", "FETCH_HEAD")
    if merged.returncode != 0:
        raise ManagedCheckoutError(f"could not fast-forward {branch}: {_reason(merged)}")
    return before, _git(repo_path, "rev-parse", "HEAD").stdout.strip()


def sync_managed_checkout(repo_path: Path | str) -> None:
    """Bring a managed checkout to its remote head; a no-op for any other checkout.

    Call it holding the repository's update lock, before the index job reads
    the tree. A :class:`ManagedCheckoutError` says why the job cannot run.
    """
    if not is_managed_checkout(repo_path):
        return
    try:
        before, after = fast_forward(repo_path)
    except ManagedCheckoutError as exc:
        logger.warning("managed_checkout_not_moved", repo_path=str(repo_path), reason=str(exc))
        raise ManagedCheckoutError(
            f"Managed checkout not updated: {exc}. The index was left as it was."
        ) from None
    if before != after:
        logger.info("managed_checkout_moved", repo_path=str(repo_path), before=before, after=after)
