"""The registry of every agent target repowise knows.

Adding an agent is a module in :mod:`.targets` plus one line in
:data:`_TARGET_MODULES`. Order here is the order agents appear in prompts, in
``--target=all``, and in listings — keep it stable.

Targets are named by ``module:attribute`` rather than imported at module scope,
mirroring the hook-adapter registry. Registration should cost nothing: a caller
resolving one target must not pay the import of every other, and this module is
reachable from ``init`` and ``doctor`` where startup time is visible.

There is deliberately no hand-written ``TargetId`` union beside this map. A
literal type listing the ids is the obvious companion and it is a third file to
keep in sync, which means it can drift from the registry it describes. Here the
ids *are* the registry keys, so there is nothing to synchronise.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .types import AgentTarget, InstallMethod, Registration, Scope, Tier, derive_tier

#: Every known target, by id. Values are ``module:attribute`` so registration
#: costs no import.
_TARGET_MODULES: dict[str, str] = {
    "claude-code": "repowise.cli.agent_targets.targets.claude_code:TARGET",
    "codex": "repowise.cli.agent_targets.targets.codex:TARGET",
    "vscode": "repowise.cli.agent_targets.targets.vscode:TARGET",
    "cursor": "repowise.cli.agent_targets.targets.cursor:TARGET",
    "opencode": "repowise.cli.agent_targets.targets.opencode:TARGET",
    "hermes": "repowise.cli.agent_targets.targets.hermes:TARGET",
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


def default_selection(rows: list[dict]) -> set[str]:
    """Which agents to pre-tick, given :func:`describe_agents` rows.

    Wired agents, installed agents, and :data:`_AUTO_FALLBACK` — always, not as
    a last resort.

    That "always" is the whole point and it is easy to get wrong: unioning in
    ``resolve_target_flag("auto")`` instead *looks* equivalent and is a no-op,
    because ``auto`` resolves to the detected targets and only reaches the
    fallback when detection is empty — which is exactly the case the union was
    already handling. A repo with a committed ``.codex/config.toml`` on a
    machine with no ``~/.claude`` still produced a non-empty selection with
    Claude Code missing from it.

    Why it matters that the fallback is unconditional: leaving an agent
    unticked is not neutral. For the agent that owns the instruction file the
    setup path persists the opt-out into ``.repowise/config.yaml``, so one
    Enter turns off a file that used to default to on and ``update`` never
    generates it again. **Detection decides what to offer; it must not silently
    withdraw a default.**
    """
    chosen = {row["id"] for row in rows if row["registrations"] or row["present"]}
    return chosen | {_AUTO_FALLBACK}


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


#: Ids being removed by the command currently running, so a shared file is not
#: kept on behalf of an agent the same invocation is also removing.
#:
#: A context variable rather than a parameter on
#: :meth:`~.types.AgentTarget.uninstall` because the fact belongs to the *run*,
#: not to the target: a descriptor is asked to remove itself and has no business
#: knowing what else the user asked for. Adding it to the Protocol would put a
#: batch concern into the contract every future target has to implement.
_REMOVING: ContextVar[frozenset[str]] = ContextVar("_REMOVING", default=frozenset())


@contextmanager
def removing(target_ids: Iterable[str]):
    """Declare which targets the current command is removing.

    Set by ``agents remove`` and ``doctor --repair`` around the whole batch.
    Without it, ``agents remove --target=all`` deadlocks on any file two agents
    share: each target's uninstall sees the other still wired -- it has not been
    processed yet, or its own config is not what detection keys on -- and keeps
    the shared block on its behalf, so both keep it and the user is told twice
    to remove an agent they just removed.
    """
    token = _REMOVING.set(frozenset(target_ids))
    try:
        yield
    finally:
        _REMOVING.reset(token)


def other_managers_of(
    config_path: Path,
    *,
    exclude: str,
    scope: Scope,
    repo_path: Path | None = None,
) -> list[str]:
    """Display names of other **wired** targets that also manage *config_path*.

    Some files are a host-neutral convention rather than one agent's private
    config. ``AGENTS.md`` is the case that forced this: Codex reads it, OpenCode
    reads it, and both descriptors legitimately manage the same path in the same
    repo. Install is unaffected, because the managed block is marker-delimited
    and idempotent, so the second writer reports ``unchanged``. **Uninstall is
    not.** Removing one of the two agents stripped the block out from under the
    other, which stayed wired and silently lost its instructions, and nothing in
    the output said so.

    So a target about to remove a shared file asks who else is still using it.
    Only *wired* targets count: a descriptor that merely knows about the path is
    not a reason to leave a block behind, and every target claims paths it has
    never written. Targets the current command is itself removing do not count
    either -- see :func:`removing`.

    Deliberately built out of :meth:`~.types.AgentTarget.detect` and
    :meth:`~.types.AgentTarget.describe_paths`, both of which already exist and
    already mean exactly this. An ``owns_path`` method on the Protocol would be
    a third thing to keep in agreement with the two that already answer the
    question.

    Paths are compared resolved, so ``AGENTS.md`` reached through a symlinked
    or differently-cased repo root still matches. Best-effort per target, by the
    same contract detection has everywhere else: a descriptor whose probe raises
    is treated as not using the file rather than taking an uninstall down.
    """
    try:
        wanted = config_path.resolve()
    except OSError:
        wanted = config_path

    ignored = _REMOVING.get() | {exclude}
    owners: list[str] = []
    for target_id in _TARGET_MODULES:
        if target_id in ignored:
            continue
        target = get_target(target_id)
        if target is None:
            continue
        try:
            if not any(r.scope is scope for r in target.detect(repo_path)):
                continue
            claimed = target.describe_paths(scope, repo_path=repo_path)
        except Exception:
            continue
        for raw in claimed:
            try:
                candidate = Path(raw).resolve()
            except OSError:
                candidate = Path(raw)
            if candidate == wanted:
                owners.append(target.display_name)
                break
    return owners


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
