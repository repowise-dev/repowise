"""Checks 11-13: distill, Claude Code MCP registration and launch, agents."""

from __future__ import annotations

import functools as _functools
from pathlib import Path as _DoctorPath

from .._types import DoctorCheck, _check
from .context import CheckContext


def integration_checks(ctx: CheckContext) -> list[DoctorCheck]:
    """Checks 11-13, in order. Sets the two flags that drive ``--repair``."""
    checks = list(_distill_checks(ctx.repo_path))
    # 12. Static registration check: wedged-path detection.
    registration_check, ctx.registration_wedged = _claude_registration_check(ctx.repo_path)
    checks.append(registration_check)
    # 12b. Does that registration actually start? Separate row: a wired-up
    # server that dies on every invocation has a different fix than none.
    if not ctx.quick:
        checks.append(_mcp_responds_check())
    # 13. Per-agent health, reported by each agent's own descriptor.
    agent_checks, ctx.agents_need_refresh = _agent_target_checks(ctx.repo_path)
    checks.extend(agent_checks)
    return checks


def _agent_target_checks(
    repo_path: _DoctorPath | None = None,
) -> tuple[list[DoctorCheck], bool]:
    """One row per agent, reported by that agent's own descriptor.

    Nothing here knows what any particular agent's health means: each
    ``AgentTarget`` answers for itself, so a fourth agent gets a doctor row by
    being registered rather than by someone adding a branch to this function.

    Only ``broken`` fails the run, and the line is drawn there on purpose.
    ``not-installed`` is not a failure — an agent you do not use is not a
    problem with your setup — and neither is ``stale``, which is advisory in
    the same way the "Distill rewrite hook" row above it already is. A stale
    matcher is common, opt-in surfaces go stale routinely, and making it fail
    would turn ``repowise doctor`` non-zero in CI for a condition nobody asked
    the tool to guarantee. ``broken`` means a config file is damaged, which is
    a real fault in the setup being checked.

    Advisory does not mean quiet. The stale row is the reason this function
    exists: a hook whose matcher names a tool the host has since renamed is
    installed, parses, and will never fire, which is indistinguishable from
    working unless something says so out loud. It is also the agreed mitigation
    for gating the self-heal migrations on ``REPOWISE_SKIP_EDITOR_SETUP`` —
    someone who exports that permanently never gets the migration, so the
    staleness has to be visible somewhere. It is printed, and it drives
    ``--repair``; it just does not change the exit code.

    Returns ``(checks, needs_refresh)``; ``needs_refresh`` drives ``--repair``.
    """
    from repowise.cli.agent_targets.registry import all_targets

    checks: list[DoctorCheck] = []
    needs_refresh = False
    try:
        targets = all_targets()
    except Exception as exc:  # pragma: no cover - a broken registry is a crash elsewhere
        row = _check(
            "Agent integrations",
            True,
            f"Could not check: {exc}",
            check_id="agent-integrations",
            warn=True,
        )
        return [row], False

    for target in targets:
        name, check_id = f"Agent: {target.id}", f"agent-{target.id}"
        try:
            report = target.doctor(repo_path=repo_path)
        except Exception as exc:
            detail = f"Could not check: {exc}"
            checks.append(_check(name, True, detail, check_id=check_id, warn=True))
            continue
        if report.status.value in ("ok", "not-installed"):
            detail = report.issues[0] if report.issues else "wired up"
            checks.append(_check(name, True, detail, check_id=check_id))
            continue
        # Only conditions the repair pass can actually resolve drive it. A
        # plugin the CLI cannot rewrite, or a fix that is a different command,
        # would otherwise buy a global config write that changes nothing and
        # then report advice for a condition the user does not have.
        needs_refresh = needs_refresh or report.repairable
        detail = "; ".join(report.issues)
        if report.fix_command:
            detail += f" (run: {report.fix_command})"
        # Stale is a warn and broken a fail; only broken changes the exit code.
        checks.append(
            _check(
                name,
                report.status.value != "broken",
                detail,
                check_id=check_id,
                warn=True,
                fix=report.fix_command or None,
            )
        )
    return checks, needs_refresh


def _registered_mcp_entry() -> dict | None:
    """The Claude Code ``mcpServers.repowise`` entry, or None if there isn't one.

    Delegates to ``claude_config``, which owns reading this entry, so the two
    doctor checks and the registration writer cannot drift apart on what counts
    as registered. Both checks must judge the same entry: a smoke check that
    launched a *different* command than the one registered would give a clean
    bill of health to the broken thing.
    """
    from repowise.cli.editor_integrations.claude_config import (
        _claude_code_settings_path,
        _stored_repowise_entry,
    )

    return _stored_repowise_entry(_claude_code_settings_path())


def _unregistered_detail() -> tuple[str, bool]:
    """``(detail, unreadable)``: why there is no entry, absent or present but unreadable.

    Those have different fixes, and collapsing them tells a user with a
    hand-broken settings.json to run ``init``, which will fail on the same
    file. Only reached when there is no entry, so it costs nothing normally.
    """
    from repowise.cli.editor_integrations.claude_config import (
        _claude_code_settings_path,
        load_existing_config,
    )

    settings_path = _claude_code_settings_path()
    if settings_path.exists():
        try:
            load_existing_config(settings_path)
        except Exception:
            return f"could not parse {settings_path}", True
    return "not registered (repowise init registers it)", False


@_functools.cache
def _mcp_responds_check() -> DoctorCheck:
    """Launch the registered MCP server and complete one round trip.

    Reported separately from the registration row: registration answers "is it
    wired up", this answers "does it run". An install whose server dies on
    every invocation passes the first and fails this one, which is the whole
    point. Absence of a registration is informational, matching
    the registration check.

    Cached for the process because there is one global ``mcpServers.repowise``
    entry and this takes no repo argument, while workspace mode runs the repo
    checks once per entry. Without the cache a ten-repo workspace would launch
    the same server ten times and pay the startup cost for each.
    """
    from ..mcp_smoke import CHECK_ID, CHECK_NAME, mcp_smoke_check

    try:
        entry = _registered_mcp_entry()
        if entry is None:
            return _check(
                CHECK_NAME, True, "not registered - nothing to launch", check_id=CHECK_ID
            )
        command = entry.get("command")
        args = entry.get("args")
        if not isinstance(command, str) or not isinstance(args, list):
            return _check(
                CHECK_NAME,
                True,
                "registration is hand-shaped - not launching it",
                check_id=CHECK_ID,
            )
        return mcp_smoke_check(command, [str(a) for a in args], entry.get("env"))
    except Exception as exc:  # a doctor check must never be the crash
        return _check(CHECK_NAME, True, f"Could not check: {exc}", check_id=CHECK_ID, warn=True)


def _claude_registration_check(
    repo_path: _DoctorPath | None = None,
) -> tuple[DoctorCheck, bool]:
    """Detect a wedged Claude Code MCP registration (stale paths).

    The global ``~/.claude/settings.json`` entry can end up pointing at a
    directory that no longer exists (a moved repo, or a leaked temp path) or
    at a pinned command binary from a deleted venv; either way the MCP
    server silently fails to start in every Claude Code session. Returns
    ``(check, wedged)``; ``wedged`` drives the ``--repair`` re-registration.
    Absence of a registration is informational, never a failure.

    With *repo_path*, an entry serving a different repo gets an advisory note
    unless another Claude Code scope registers this repo. Advisory because the
    entry is not broken, and never repaired: repointing the one global entry
    would break the repo it serves.

    This is a *static* check. Whether the server actually starts is
    ``_mcp_responds_check``.
    """
    name, check_id = "Claude Code MCP entry", "claude-code-mcp-entry"
    try:
        entry = _registered_mcp_entry()
        if entry is None:
            # An unparseable settings file is worth a look; no entry at all is
            # a choice (another agent, or none).
            detail, unreadable = _unregistered_detail()
            return _check(name, True, detail, check_id=check_id, warn=unreadable), False

        problems: list[str] = []
        command = entry.get("command")
        # Bare command names resolve via PATH at session start; only a pinned
        # absolute path can go stale.
        if (
            isinstance(command, str)
            and ("/" in command or "\\" in command)
            and not _DoctorPath(command).exists()
        ):
            problems.append(f"command not found: {command}")
        target = _registration_target(entry)
        if target is not None and not _DoctorPath(target).is_dir():
            problems.append(f"registered path missing: {target}")
        if problems:
            detail = "; ".join(problems) + " (rerun with --repair)"
            row = _check(name, False, detail, check_id=check_id, fix="repowise doctor --repair")
            return row, True
        if target is not None and repo_path is not None and not _registers_repo(target, repo_path):
            add = "repowise agents add --target=claude-code --scope=project"
            detail = (
                f"serves {target}; this repo is not registered (run `{add}` for a project entry)"
            )
            return _check(name, True, detail, check_id=check_id, warn=True, fix=add), False
        return _check(name, True, target or "registered", check_id=check_id), False
    except Exception as exc:
        return _check(name, True, f"Could not check: {exc}", check_id=check_id, warn=True), False


def _registers_repo(target: str, repo_path: _DoctorPath) -> bool:
    """Whether Claude Code serves *repo_path* from the global entry or another scope.

    The global *target* counts when it is what init registers for this repo
    (the workspace root inside a workspace). Otherwise a repowise entry naming
    this repo in ``.mcp.json`` (project scope) or ``~/.claude.json`` (user or
    local scope) counts; one copied from another repo does not.
    """
    from repowise.cli.agent_targets.targets.claude_code import project_mcp_config_path
    from repowise.cli.editor_integrations.claude_config import (
        _resolve_mcp_target,
        _stored_repowise_entry,
    )
    from repowise.cli.helpers import _same_path

    served = (repo_path, _resolve_mcp_target(repo_path))

    def serves(path: str) -> bool:
        # Claude Code starts a project server in the repo, so a relative path
        # is relative to it.
        return any(_same_path(repo_path / path, s) for s in served)

    def names_repo(entry: object) -> bool:
        path = _registration_target(entry) if isinstance(entry, dict) else None
        return path is not None and serves(path)

    if serves(target):
        return True
    other_scopes = [
        _stored_repowise_entry(project_mcp_config_path(repo_path)),
        *_claude_json_entries(repo_path),
    ]
    return any(names_repo(e) for e in other_scopes)


def _claude_json_entries(repo_path: _DoctorPath) -> list[object]:
    """Repowise entries in ``~/.claude.json``: user scope, then this repo's local scope.

    Read here only, best effort: an unreadable file yields nothing, so the
    worst case is the advisory note above.
    """
    import json

    from repowise.cli.helpers import _same_path

    try:
        data = json.loads((_DoctorPath.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    scopes = [data]
    projects = data.get("projects")
    if isinstance(projects, dict):
        scopes += [
            v
            for k, v in projects.items()
            if isinstance(v, dict) and _same_path(_DoctorPath(k), repo_path)
        ]
    entries: list[object] = []
    for scope in scopes:
        servers = scope.get("mcpServers")
        if isinstance(servers, dict) and "repowise" in servers:
            entries.append(servers["repowise"])
    return entries


def _registration_target(entry: dict) -> str | None:
    """The repo/workspace path a registration serves, or None if unrecognized.

    Registrations are shaped ``args: ["mcp", "<abs path>", "--transport",
    "stdio"]``; the target sits right after ``mcp``. Anything else (a flag
    in that slot, a hand-edited entry) returns None rather than guessing.
    """
    args = entry.get("args")
    if not isinstance(args, list) or args[:1] != ["mcp"] or len(args) < 2:
        return None
    target = args[1]
    if isinstance(target, str) and not target.startswith("-"):
        return target
    return None


def _distill_config_row(repo_path: _DoctorPath) -> tuple[DoctorCheck, object]:
    """``(row, distill_cfg)``; the block is kept even when validating it fails."""
    distill_cfg = None
    try:
        from repowise.core.repo_config import load_repo_config

        distill_cfg = load_repo_config(repo_path).get("distill")
        from repowise.core.distill.config import validate_distill_config

        problems = validate_distill_config(distill_cfg)
        if problems:
            row = _check("Distill config", False, "; ".join(problems), check_id="distill-config")
            return row, distill_cfg
        detail = "valid" if distill_cfg else "defaults (no block)"
        return _check("Distill config", True, detail, check_id="distill-config"), distill_cfg
    except Exception as exc:
        row = _check(
            "Distill config", True, f"Could not check: {exc}", check_id="distill-config", warn=True
        )
        return row, distill_cfg


def _omission_store_row(repo_path: _DoctorPath, distill_cfg: object) -> DoctorCheck:
    """TTL and cap are pruned on write, so a store far past its cap means
    pruning is not keeping up."""
    try:
        from repowise.core.distill.config import omission_store_settings

        _ttl_days, max_mb = omission_store_settings(distill_cfg)
        db_path = repo_path / ".repowise" / "omissions" / "omissions.db"
        if not db_path.is_file():
            return _check("Omission store", True, "not created yet", check_id="omission-store")
        size_bytes = db_path.stat().st_size
        wal = db_path.with_name(db_path.name + "-wal")
        if wal.is_file():
            size_bytes += wal.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        ok = size_mb <= max_mb * 1.5  # WAL slack before checkpointing
        detail = f"{size_mb:.1f} MB (cap {max_mb:g} MB)"
        if not ok:
            detail += " — over cap; pruning happens on the next distill write"
        return _check("Omission store", ok, detail, check_id="omission-store")
    except Exception as exc:
        return _check(
            "Omission store", True, f"Could not check: {exc}", check_id="omission-store", warn=True
        )


def _rewrite_hook_surfaces() -> list[tuple[str, object]]:
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
    from repowise.cli.agent_adapters.codex import CodexAdapter

    surfaces = [("claude-code", ClaudeCodeAdapter())]
    codex = CodexAdapter()
    if codex.detect():
        surfaces.append(("codex", codex))
    from repowise.cli.agent_adapters.cursor import CursorAdapter

    cursor = CursorAdapter()
    if cursor.detect():
        surfaces.append(("cursor", cursor))
    return surfaces


def _installed_rewrite_hooks(surfaces: list[tuple[str, object]]) -> tuple[list[str], bool]:
    """``(names, any_unmatched)`` for the surfaces with the hook installed."""
    # Registered and live are different questions: an entry whose matcher
    # predates a tool rename is registered and fires on some or none of the
    # agent's shell tools. Say which, rather than calling all three "installed".
    installed_names = []
    any_unmatched = False
    for name, adapter in surfaces:
        status = adapter.rewrite_hook_status()  # type: ignore[attr-defined]
        if not status.installed:
            continue
        if not status.unmatched:
            installed_names.append(name)
        else:
            any_unmatched = True
            misses = ", ".join(status.unmatched)
            installed_names.append(f"{name} (matcher misses {misses})")
    return installed_names, any_unmatched


def _rewrite_hook_row(distill_cfg: object) -> DoctorCheck:
    """Advisory: the rewrite hook is strictly opt-in. A matcher that misses a
    shell tool is a warn: installed but not firing there."""
    name, check_id = "Distill rewrite hook", "distill-rewrite-hook"
    try:
        installed_names, any_unmatched = _installed_rewrite_hooks(_rewrite_hook_surfaces())
        if installed_names:
            commands_cfg = distill_cfg.get("commands") if isinstance(distill_cfg, dict) else None
            opted_out = isinstance(commands_cfg, dict) and commands_cfg.get("enabled") is False
            detail = ", ".join(installed_names)
            detail += " (this repo opted out)" if opted_out else ""
            detail = f"installed: {detail}"
            if any_unmatched:
                fix = "repowise hook rewrite install"
                return _check(name, True, detail, check_id=check_id, warn=True, fix=fix)
        else:
            detail = "not installed (opt-in: repowise hook rewrite install)"
        return _check(name, True, detail, check_id=check_id)
    except Exception as exc:
        return _check(name, True, f"Could not check: {exc}", check_id=check_id, warn=True)


def _distill_checks(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """Distill feature health: config validity, store sizing, rewrite hook.

    The rewrite hook is opt-in, so its absence is informational, never a
    failure. Any unexpected error degrades to an advisory OK row — distill
    problems must not break doctor.
    """
    config_row, distill_cfg = _distill_config_row(repo_path)
    return [
        config_row,
        _omission_store_row(repo_path, distill_cfg),
        _rewrite_hook_row(distill_cfg),
    ]
