"""The registry of every agent target repowise knows.

Adding an agent is a module in :mod:`.targets` plus one line in
:data:`_TARGET_MODULES`. Order here is the order agents appear in prompts, in
``--target=all``, and in listings — keep it stable.

Targets are named by ``module:attribute`` rather than imported at module scope,
mirroring the hook-adapter registry. Registration should cost nothing: a caller
resolving one target must not pay the import of every other, and this module is
reachable from ``init`` and ``doctor`` where startup time is visible.

One deviation from codegraph, deliberate: their ``TargetId`` is a hand-written
string union, so adding a target touches three files instead of two and the
union can drift from the registry. Here the ids *are* the registry keys, so
there is nothing to keep in sync.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from .types import AgentTarget, Registration, Scope, Tier, derive_tier

#: Every known target, by id. Values are ``module:attribute`` so registration
#: costs no import.
_TARGET_MODULES: dict[str, str] = {
    "claude-code": "repowise.cli.agent_targets.targets.claude_code:TARGET",
    "codex": "repowise.cli.agent_targets.targets.codex:TARGET",
    "vscode": "repowise.cli.agent_targets.targets.vscode:TARGET",
}

#: Resolved when :meth:`resolve_target_flag` is asked for ``auto`` and nothing
#: is detected. Least surprise for existing users, who are overwhelmingly here.
_AUTO_FALLBACK = "claude-code"


def list_target_ids() -> list[str]:
    """Every known target id, in registry order."""
    return list(_TARGET_MODULES)


def get_target(target_id: str) -> AgentTarget | None:
    """The target for *target_id*, or None when it is not a known id."""
    spec = _TARGET_MODULES.get(target_id)
    if spec is None:
        return None
    module_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def all_targets() -> list[AgentTarget]:
    """Every target, in registry order. Imports all of them, so call sparingly."""
    return [target for target in (get_target(tid) for tid in _TARGET_MODULES) if target]


def tier_of(target_id: str) -> Tier | None:
    """The derived support tier for *target_id*."""
    target = get_target(target_id)
    return derive_tier(target) if target else None


def detect_all(repo_path: Path | None = None) -> dict[str, list[Registration]]:
    """Run detection for every target, keyed by target id.

    Returns a registration *list* per target, never a bool, so the caller can
    report "wired twice" rather than "wired". Detection is best-effort by
    contract: a target whose probe raises is reported as having no
    registrations rather than taking the whole listing down with it.
    """
    found: dict[str, list[Registration]] = {}
    for target_id in _TARGET_MODULES:
        target = get_target(target_id)
        if target is None:
            continue
        try:
            found[target_id] = list(target.detect(repo_path))
        except Exception:
            found[target_id] = []
    return found


def resolve_target_flag(value: str, repo_path: Path | None = None) -> list[AgentTarget]:
    """Resolve a ``--target=`` value to targets.

    Accepts ``auto`` (everything detected, falling back to Claude Code when
    nothing is), ``all``, ``none``, or a comma-separated list of ids. An unknown
    id raises with the full list of known ones, because the alternative — a
    typo silently resolving to nothing and the command reporting success — is
    the failure mode that wastes the most of a user's time.
    """
    normalized = (value or "").strip().lower()
    if normalized == "none":
        return []
    if normalized == "all":
        return all_targets()
    if normalized == "auto":
        detected = detect_all(repo_path)
        resolved = [
            target
            for target_id, registrations in detected.items()
            if registrations and (target := get_target(target_id)) is not None
        ]
        if resolved:
            return resolved
        fallback = get_target(_AUTO_FALLBACK)
        return [fallback] if fallback else []

    ids = [part.strip() for part in normalized.split(",") if part.strip()]
    resolved = []
    unknown = []
    for target_id in ids:
        target = get_target(target_id)
        if target is None:
            unknown.append(target_id)
        else:
            resolved.append(target)
    if unknown:
        known = ", ".join(list_target_ids())
        raise ValueError(
            f"Unknown --target id(s): {', '.join(unknown)}. "
            f"Known: {known}, plus 'auto' / 'all' / 'none'."
        )
    return resolved


def targets_for_scope(scope: Scope) -> list[AgentTarget]:
    """Every target that has a config home at *scope*."""
    return [target for target in all_targets() if target.supports_scope(scope)]
