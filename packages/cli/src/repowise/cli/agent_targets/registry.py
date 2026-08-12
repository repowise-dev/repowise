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

from .types import AgentTarget, InstallMethod, Registration, Tier, derive_tier

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


def describe_agents(repo_path: Path | None = None) -> list[dict]:
    """One row per registered target: what it is, and where it is wired.

    The projection both consumers read — ``repowise agents``' JSON payload and
    table, and ``init``'s checklist. Built here, next to the descriptors, so
    there is one answer to "what do we know about this agent" rather than one
    per caller that drift into disagreeing about which are pre-ticked.

    Every probe is best-effort: a descriptor whose detection or presence check
    raises is reported as absent rather than taking the listing down.
    """
    rows: list[dict] = []
    for target in all_targets():
        try:
            registrations = list(target.detect(repo_path))
        except Exception:
            registrations = []
        try:
            present = bool(target.is_present(repo_path))
        except Exception:
            present = False
        #: None here means "already provided by a host-managed install", which
        #: is the stand-down the method axis exists for — not "cannot install".
        method = select_install_method(target, registrations)
        rows.append(
            {
                "id": target.id,
                "display_name": target.display_name,
                "tier": tier_of(target.id).value,
                "docs_url": target.docs_url,
                "project_file_id": target.project_file_id,
                "present": present,
                "method": method.id if method is not None else None,
                "registrations": [r.as_dict() for r in registrations],
            }
        )
    return rows


def default_selection(rows: list[dict], repo_path: Path | None = None) -> set[str]:
    """Which agents to pre-tick, given :func:`describe_agents` rows.

    Wired agents, installed agents, **and** the fallback ``--target=auto``
    resolves to, unioned rather than used only as a last resort.

    The union is the part that matters and it was a bug before. Leaving an
    agent unticked is not neutral: for the agent that owns the instruction
    file, the setup path persists the opt-out into ``.repowise/config.yaml``,
    so ``update`` never generates it either. A machine with VS Code and Codex
    but no ``~/.claude`` produced a non-empty selection, which meant the
    fallback never fired, which meant Claude Code arrived unticked and one
    Enter permanently turned off a file that used to default to on.

    Detection decides what to *offer*; it must not silently withdraw a default.
    """
    chosen = {row["id"] for row in rows if row["registrations"] or row["present"]}
    return chosen | {target.id for target in resolve_target_flag("auto", repo_path)}


def select_install_method(
    target: AgentTarget,
    registrations: list[Registration],
) -> InstallMethod | None:
    """The method to install *target* through, or None to stand down.

    This is where the method axis pays for itself. Claude Code is reachable two
    ways — its host plugin, and repowise writing the config directly — and both
    register the same MCP server and the same augment hooks. The host merges
    them without complaint, so the user gets no error, just two process spawns
    per matched tool call and a duplicate set of tool schemas resident in every
    session. Measured on a live machine: three repowise MCP servers at once,
    ~36 tool schemas for one product.

    So: when *registrations* show a host-managed method already in place, return
    None. There is nothing for us to write, and writing anyway is how the
    duplicate happens. Otherwise return the method marked ``preferred`` among
    the ones repowise can actually write, falling back to the first of those.

    Deliberately not applied by ``init``: standing down there would change what
    a plugin user's ``init`` does today, and this decision is about cost rather
    than correctness. ``agents add`` and ``agents refresh`` own it.
    """
    detected = {r.method for r in registrations}
    writable = [m for m in target.methods if m.managed_by != "host"]
    for method in target.methods:
        if method.managed_by == "host" and method.id in detected:
            return None
    for method in writable:
        if method.preferred:
            return method
    return writable[0] if writable else None


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
