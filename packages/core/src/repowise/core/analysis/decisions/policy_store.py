"""Reading and writing the decision policy in ``.repowise/config.yaml``.

Kept apart from :mod:`policy` so resolution stays a pure function: this is the
only module in the layer that touches the filesystem.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from repowise.core.repo_config import load_repo_config, save_repo_config

from .policy import PRESETS, DecisionPolicy, PolicyResolution, resolve_policy

__all__ = ["PolicyConflictError", "load_policy", "policy_etag", "write_policy"]


class PolicyConflictError(RuntimeError):
    """A policy write lost a race with another writer."""


def load_policy(repo_path: Path | str) -> PolicyResolution:
    """Resolve the policy for a repo, warnings included."""
    return resolve_policy(load_repo_config(repo_path))


def policy_etag(policy: DecisionPolicy) -> str:
    """A short hash of the resolved policy, for optimistic concurrency.

    Derived from the resolved block rather than the config file so unrelated
    edits (a provider change, a coverage path) do not invalidate a settings
    form the user has open.
    """
    payload = json.dumps(policy.to_config_block(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def write_policy(
    repo_path: Path | str,
    policy: DecisionPolicy,
    *,
    expected_etag: str | None = None,
) -> PolicyResolution:
    """Persist *policy*, preserving every unrelated key, and re-resolve it.

    Validates by round-tripping: the block is resolved back before the write,
    and a mismatch raises rather than persisting something that would read as a
    different policy than the caller set. With *expected_etag*, a concurrent
    change to the same block raises :class:`PolicyConflictError` and writes nothing.
    """
    config = load_repo_config(repo_path)

    if expected_etag is not None:
        current = resolve_policy(config).policy
        if policy_etag(current) != expected_etag:
            raise PolicyConflictError(
                "The decision settings changed since they were read. Reload and retry."
            )

    block = policy.to_config_block()
    round_tripped = resolve_policy({"decisions": block}).policy
    if round_tripped != policy:
        raise ValueError("Resolved policy does not round-trip through the config block.")

    merged: dict[str, Any] = dict(config)
    existing = merged.get("decisions")
    decisions = dict(existing) if isinstance(existing, dict) else {}
    # `session_mining` is now expressed as `sources.session`; leaving it behind
    # would let a stale legacy key contradict the block written above.
    decisions.pop("session_mining", None)
    decisions.update(block)
    preset = policy.preset_name()
    if preset in PRESETS:
        decisions["preset"] = preset
    else:
        decisions.pop("preset", None)
    merged["decisions"] = decisions

    save_repo_config(repo_path, merged)
    return resolve_policy(merged)
