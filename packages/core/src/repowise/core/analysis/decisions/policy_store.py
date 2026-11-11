"""Reading and writing the decision policy in ``.repowise/config.yaml``.

Kept apart from :mod:`policy` so resolution stays a pure function: this is the
only module in the layer that touches the filesystem.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from repowise.core.repo_config import load_repo_config, save_repo_config

from .policy import (
    MODE_DEFAULTS,
    POLICY_CONFIG_KEYS,
    PRESETS,
    DecisionPolicy,
    PolicyResolution,
    resolve_policy,
)

__all__ = [
    "PolicyConflictError",
    "layer_mode",
    "load_policy",
    "policy_etag",
    "write_policy",
    "write_setting",
]


class PolicyConflictError(RuntimeError):
    """A policy write lost a race with another writer."""


def load_policy(repo_path: Path | str) -> PolicyResolution:
    """Resolve the policy for a repo, warnings included."""
    return resolve_policy(load_repo_config(repo_path))


def layer_mode(repo_path: Path | str | None) -> str:
    """``decisions.mode`` for *repo_path*: ``off``, ``read_only`` or ``full``.

    For the read surfaces (hooks, MCP tools, generated agent files), which must
    never fail over a setting: no path or an unreadable config reads as
    ``full``, the behaviour of a repository that never set one, and
    ``decision config show`` reports the problem.
    """
    if not repo_path:
        return "full"
    try:
        return load_policy(repo_path).policy.mode
    except Exception:
        return "full"


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
    # Every key the policy owns is replaced, not merged over, or a setting
    # `to_config_block` omitted at its default keeps its stored value forever.
    # Unrelated keys under `decisions:` survive, which is what the merge is for.
    decisions = {
        k: v
        for k, v in (existing.items() if isinstance(existing, dict) else ())
        if k not in POLICY_CONFIG_KEYS
    }
    decisions.update(block)
    preset = policy.preset_name()
    if preset in PRESETS:
        decisions["preset"] = preset
    else:
        decisions.pop("preset", None)
    merged["decisions"] = decisions

    save_repo_config(repo_path, merged)
    return resolve_policy(merged)


def write_setting(repo_path: Path | str, key: str, value: Any) -> None:
    """Write one ``decisions.*`` setting; ``None`` puts it back to its default.

    The writer :mod:`repowise.core.settings` calls for this area. The change
    means what :meth:`DecisionPolicy.with_setting` says (a new mode resets the
    switches under it), and only the keys it needs are written: a set followed
    by a reset leaves the file as it was. When a small edit cannot say the
    same policy, the whole block is written by :func:`write_policy` instead.
    """
    name = key.removeprefix("decisions.")
    config = load_repo_config(repo_path)
    target = resolve_policy(config).policy.with_setting(name, value)
    edited = _settle_update_mining(config, _edited_config(config, name, value), target)
    if edited == config:
        return
    if resolve_policy(edited).policy == target:
        save_repo_config(repo_path, edited)
    else:
        write_policy(repo_path, target)


def _settle_update_mining(
    before: dict[str, Any], config: dict[str, Any], target: DecisionPolicy
) -> dict[str, Any]:
    """Pin or drop ``update_mining`` so the edited *config* still reads as *target*.

    A block that gains ``sources`` without ``update_mining`` reads it as on
    (the upgrade rule), so the edit states it; an edit that removes the last
    source no longer needs it stated, so a reset drops it again.
    """
    had_sources = "sources" in (before.get("decisions") or {})
    block = config.get("decisions")
    if not isinstance(block, dict):
        return config
    if resolve_policy(config).policy.update_mining != target.update_mining:
        return {**config, "decisions": {**block, "update_mining": target.update_mining}}
    if had_sources and "sources" not in block and block.get("update_mining") is False:
        trimmed = {k: v for k, v in block.items() if k != "update_mining"}
        candidate = {**config, "decisions": trimmed} if trimmed else {
            k: v for k, v in config.items() if k != "decisions"
        }
        if resolve_policy(candidate).policy == target:
            return candidate
    return config


#: Keys a mode or a preset decides, dropped when one is set so they stop overriding it.
_MODE_OWNED = tuple(MODE_DEFAULTS["full"])
_PRESET_OWNED = ("enabled", "llm", "sources", "session_mining")


def _edited_config(config: dict[str, Any], name: str, value: Any) -> dict[str, Any]:
    """*config* with ``decisions.<name>`` set to *value*, or removed for ``None``."""
    edited = copy.deepcopy(config)
    raw = edited.get("decisions")
    block: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    parts = name.split(".")
    owned = {"mode": _MODE_OWNED, "preset": _PRESET_OWNED}.get(parts[0], ())
    for owned_key in owned:
        block.pop(owned_key, None)
    if parts[0] == "mode" and value == "full":
        value = None  # the default mode is written by leaving it out
    _set_path(block, parts, value)
    if block:
        edited["decisions"] = block
    else:
        edited.pop("decisions", None)
    return edited


def _set_path(node: dict[str, Any], parts: list[str], value: Any) -> None:
    head, *rest = parts
    if not rest:
        if value is None:
            node.pop(head, None)
        else:
            node[head] = value
        return
    child = node.get(head)
    if isinstance(child, bool):
        child = {"enabled": child}  # ``sources.<k>: true`` is the short form
    child = dict(child) if isinstance(child, dict) else {}
    _set_path(child, rest, value)
    if child:
        node[head] = child
    else:
        node.pop(head, None)
