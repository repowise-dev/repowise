"""Interactive prompts shared by the single-repo and workspace init flows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from repowise.cli.ui.brand import OK, WARN
from repowise.cli.ui.glyphs import glyph


def offer_distill_rewrite_hook(
    console_obj: Any,
    repo_paths: list[Path],
    flag: bool | None,
    *,
    yes: bool = False,
    no_editor_setup: bool = False,
) -> None:
    """Install the distill command-rewrite hook (Claude Code), on by default.

    ``flag`` is the resolved ``--distill-hook/--no-distill-hook`` value:
    True installs without prompting, False skips AND gates the repos off in
    config (so a hook installed globally from another repo stays inert
    there), None prompts when interactive (defaulting to yes) and installs
    otherwise (``--yes`` or no terminal), printing the line that undoes it:
    the consent pattern the post-commit hook uses for a default that is on.

    An undecided run never overrides a recorded opt-out. A key the repo's
    config already holds is kept, and when every repo opted out of rewriting
    nothing is installed and nothing is asked.

    The hook itself is user-level (one install covers every repo), but the
    verdict is recorded per repo as ``distill.commands.enabled`` in each
    entry of ``repo_paths`` — one repo for the single-repo init flow, every
    selected repo for the workspace flow. Recording the verdict everywhere
    matters because the hook treats any repo with ``.repowise/`` and no
    config as enabled (with the ``ask`` posture).

    Installing the hook writes user-level config, so ``--no-editor-setup`` (or
    the equivalent env var) suppresses both the install and the prompt. An
    explicit ``--no-distill-hook`` still records its opt-out: that record is
    repo-local, and it is the only thing that gates an already-installed global
    hook off *here*, so dropping it would leave the hook rewriting commands in
    a repo the user just opted out of.
    """
    from repowise.cli.editor_setup import is_editor_setup_disabled

    if not repo_paths:
        return

    if is_editor_setup_disabled(no_editor_setup):
        if flag:
            console_obj.print(
                "  [dim]Rewrite hook not installed: editor setup is off for this "
                "run. Run 'repowise hook rewrite install' to set it up.[/dim]"
            )
        elif flag is False:
            _record_distill_verdict(console_obj, repo_paths, enabled=False)
        # `flag is None` decided nothing, so it writes nothing.
        return

    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    if flag is None:
        _apply_default_verdict(console_obj, adapter, repo_paths, yes=yes)
    else:
        _apply_verdict(console_obj, adapter, repo_paths, enabled=flag)


def _apply_default_verdict(
    console_obj: Any,
    adapter: Any,
    repo_paths: list[Path],
    *,
    yes: bool,
) -> None:
    """Resolve an undecided ``--distill-hook``: ask when someone can answer, else on."""
    if not adapter.detect():
        return
    if all(_distill_opted_out(rp) for rp in repo_paths):
        console_obj.print(
            "  [dim]Rewrite hook left off: this repo opted out. Run "
            "'repowise hook rewrite install' to turn it on.[/dim]"
        )
        return
    if yes or not sys.stdin.isatty():
        _apply_verdict(console_obj, adapter, repo_paths, enabled=True, keep_recorded=True)
        console_obj.print(
            "  [dim]Hooks now compact what your agent sees: noisy commands run "
            "through `repowise distill`, and whole-file Reads, many-file greps and "
            "unchanged re-reads come back shortened with a way to get the rest. "
            "Run 'repowise hook rewrite uninstall' or re-run init with "
            "--no-distill-hook to turn them off.[/dim]"
        )
        return
    enabled = _ask_hooks_question(console_obj, len(repo_paths))
    if enabled is not None:
        _apply_verdict(console_obj, adapter, repo_paths, enabled=enabled)


def _ask_hooks_question(console_obj: Any, repo_count: int) -> bool | None:
    """Ask the one hooks question; None when the terminal cannot answer."""
    console_obj.print()
    console_obj.print(
        "[bold]Distill:[/bold] Rewrite noisy agent commands (tests, builds, "
        "git, searches) to `repowise distill ...` for compact output?"
    )
    scope = f"Applies to all {repo_count} selected repos. " if repo_count > 1 else ""
    console_obj.print(
        f"  [dim]{scope}Rewrites run without a prompt and only ever wrap a "
        "recognized command; set `permission: ask` in .repowise/config.yaml to "
        "approve each one. Raw output stays recoverable via `repowise expand`.[/dim]"
    )
    # Named here because a yes turns them on. The question stays one
    # question: this is the same consent, and the thing being consented to
    # is "hooks may compact what your agent sees", not three features.
    # Every surface a yes covers has to be named, or it is not consent.
    console_obj.print(
        "  [dim]Also shrinks three big tool results, all reversible: a "
        "whole-file Read comes back as a skeleton, a grep matching many "
        "files comes back as a per-file summary, and re-reading a file you "
        "already read — unchanged, unedited — comes back as a one-line "
        "pointer instead of the content. Each says how to get the rest, and "
        "reading again always returns it. Toggle later: "
        "`repowise hook read-skeleton`, `repowise hook search-digest`, "
        "`repowise hook read-reread`.[/dim]"
    )
    try:
        return click.confirm("  Install the Claude Code rewrite hook?", default=True)
    except (click.Abort, EOFError):
        # Same unanswerable-terminal case as the post-commit hook offer
        # above: decline an optional extra rather than fail a finished run.
        console_obj.print(
            "\n  [dim]Skipped. Run 'repowise hook rewrite install' later to set up.[/dim]"
        )
        return None


def _apply_verdict(
    console_obj: Any,
    adapter: Any,
    repo_paths: list[Path],
    *,
    enabled: bool,
    keep_recorded: bool = False,
) -> None:
    """Install (or skip) the user-level hook, then record the verdict per repo."""
    if enabled:
        path = adapter.install_rewrite_hook()
        if path:
            console_obj.print(f"  [{OK}]{glyph('ok')}[/] Rewrite hook installed ({path})")
        else:
            console_obj.print(f"  [{WARN}]Rewrite hook install failed.[/]")
    else:
        console_obj.print(
            "  [dim]Skipped. Run 'repowise hook rewrite install' later to set up.[/dim]"
        )
    _record_distill_verdict(console_obj, repo_paths, enabled=enabled, keep_recorded=keep_recorded)


def _distill_opted_out(repo_path: Path) -> bool:
    """True when this repo's config records ``distill.commands.enabled: false``."""
    return _recorded_hook_verdicts(repo_path).get("distill") is False


def _recorded_hook_verdicts(repo_path: Path) -> dict[str, bool]:
    """The hook verdicts this repo's config holds, keyed ``distill`` or by surface."""
    from repowise.cli.helpers import load_config

    cfg = load_config(repo_path)
    hooks = cfg.get("hooks")
    found = (
        {k: v for k, v in hooks.items() if isinstance(v, bool)} if isinstance(hooks, dict) else {}
    )
    distill = cfg.get("distill")
    commands = distill.get("commands") if isinstance(distill, dict) else None
    if isinstance(commands, dict) and isinstance(commands.get("enabled"), bool):
        found["distill"] = commands["enabled"]
    return found


def _record_distill_verdict(
    console_obj: Any,
    repo_paths: list[Path],
    *,
    enabled: bool,
    keep_recorded: bool = False,
) -> None:
    """Persist the hook-intervention verdict for every repo in this run.

    ``keep_recorded`` keeps every key the repo already holds, so an undecided
    run never turns back on a surface someone switched off by hand.

    Several keys, one answer. ``distill.commands.enabled`` gates rewriting a
    Bash command into ``repowise distill``; ``hooks.read_skeleton`` gates
    serving an unbounded Read of a large indexed file as its skeleton;
    ``hooks.search_digest`` gates serving a multi-file grep flood as its
    compact digest. All are the same consent, "repowise's hooks may intervene
    in my agent's tool calls", and the one the user was asked is the broadest
    of them, so a second prompt would be asking permission we already have.

    Read-skeleton shipped with no code path that wrote its key at all, which
    left it reachable only by hand-editing YAML. That is not a discovery
    problem: its gate needs 50 firings across 10 sessions to decide whether it
    stays, and a feature nobody can turn on returns zero of them and settles
    nothing. Every replacing surface added since rides this same verdict for
    that reason.
    """

    from repowise.cli.helpers import (
        HOOK_REPLACEMENT_SURFACES,
        save_distill_commands_enabled,
        save_hook_surface_enabled,
    )

    for repo_path in repo_paths:
        try:
            recorded = _recorded_hook_verdicts(repo_path) if keep_recorded else {}
            if "distill" not in recorded:
                save_distill_commands_enabled(repo_path, enabled=enabled)
            for surface in HOOK_REPLACEMENT_SURFACES:
                if surface not in recorded:
                    save_hook_surface_enabled(repo_path, surface, enabled=enabled)
        except Exception as exc:  # init must not crash on a config write
            console_obj.print(
                f"  [{WARN}]Could not record hook verdict for {repo_path.name}: {exc}[/]"
            )


def offer_hook_install(
    console_obj: Any,
    repo_paths: list[Path],
    aliases: list[str] | None = None,
    *,
    flag: bool | None = None,
    yes: bool = False,
    no_editor_setup: bool = False,
) -> None:
    """Install the post-commit auto-sync hook, asking only when someone can answer.

    ``flag`` is the resolved ``--hook/--no-hook`` value. ``True`` installs
    without a prompt; ``False`` skips and says how to install later; ``None``
    prompts when interactive and otherwise installs and prints one line naming
    ``repowise hook uninstall``, which is the consent pattern for a default
    that is on. ``--yes`` and a missing terminal both take that install path:
    they are the routes the product recommends, and a hook they silently
    skipped is why most indexes went stale.

    A git hook is a write outside ``.repowise/``, so ``--no-editor-setup`` (or
    ``REPOWISE_SKIP_EDITOR_SETUP``) keeps it off, the way it keeps the distill
    rewrite hook off; only an explicit ``--hook`` is told why nothing happened.
    A failed install is reported and never fails the run: the index and wiki
    are already written by the time this runs.
    """
    from repowise.cli.editor_setup import is_editor_setup_disabled

    if not repo_paths:
        return

    if is_editor_setup_disabled(no_editor_setup):
        if flag:
            console_obj.print(
                "  [dim]Post-commit hook not installed: editor setup is off for this "
                "run. Run 'repowise hook install' to set it up.[/dim]"
            )
        return

    if flag is False:
        console_obj.print(
            "  [dim]Skipped the post-commit hook. Run 'repowise hook install' later "
            "to set it up.[/dim]"
        )
        return

    if flag is None and not yes and sys.stdin.isatty():
        try:
            _offer_hook_install_prompts(console_obj, repo_paths, aliases)
        except (click.Abort, EOFError):
            # isatty() claimed a terminal that cannot answer (Windows Git Bash
            # ``< /dev/null``, pty wrappers, ``docker run -t`` without -i). This
            # is the last step of a run whose index and wiki are already
            # written, and an optional hook is not worth failing it over:
            # decline and exit clean. Ctrl-C lands here too.
            console_obj.print(
                "\n  [dim]Skipped the hook. Run 'repowise hook install' later to set it up.[/dim]"
            )
        return

    installed = 0
    for i, rp in enumerate(repo_paths):
        label = aliases[i] if aliases else rp.name
        if _install_hook(console_obj, rp, label):
            installed += 1
    if installed:
        undo = "repowise hook uninstall" + (" --workspace" if len(repo_paths) > 1 else "")
        console_obj.print(
            f"  [dim]Auto-sync is on: repowise update runs after each commit. "
            f"Run '{undo}' to turn it off.[/dim]"
        )


def _install_hook(console_obj: Any, repo_path: Path, label: str) -> bool:
    """Install one repo's hook and report it. Returns whether it is in place."""
    from repowise.cli.hooks import install

    try:
        result = install(repo_path)
    except Exception as exc:  # a hook is not worth failing a finished init over
        console_obj.print(f"  [{WARN}]{label}: post-commit hook not installed ({exc})[/]")
        return False
    if result.startswith("not "):
        console_obj.print(f"  [dim]{label}: no post-commit hook, {result}.[/dim]")
        return False
    console_obj.print(f"  [{OK}]{glyph('ok')}[/] {label}: post-commit hook {result}")
    return True


def _offer_hook_install_prompts(
    console_obj: Any,
    repo_paths: list[Path],
    aliases: list[str] | None,
) -> None:
    """Ask which repos get a post-commit hook, and install it. Prompts."""
    from repowise.cli.hooks import status

    # Filter to repos that don't already have the hook
    candidates: list[tuple[Path, str]] = []
    for i, rp in enumerate(repo_paths):
        label = aliases[i] if aliases else rp.name
        if not status(rp).startswith("installed"):
            candidates.append((rp, label))

    if not candidates:
        return  # All already have hooks

    console_obj.print()
    console_obj.print(
        "[bold]Auto-sync:[/bold] Install a post-commit hook to keep the index "
        "in sync after every commit?"
    )

    if len(candidates) == 1:
        rp, label = candidates[0]
        if click.confirm(f"  Install post-commit hook for {label}?", default=True):
            _install_hook(console_obj, rp, label)
        else:
            console_obj.print("  [dim]Skipped. Run 'repowise hook install' later to set up.[/dim]")
    else:
        # Workspace: show checkboxes-style selection
        console_obj.print("  Select repos (enter numbers, comma-separated, or 'all'):")
        for i, (_rp, label) in enumerate(candidates, 1):
            console_obj.print(f"    [{i}] {label}")

        raw = click.prompt(
            "  Repos",
            default="all",
            show_default=True,
        )
        if raw.strip().lower() == "all":
            selected_indices = list(range(len(candidates)))
        elif raw.strip().lower() in ("none", "skip", ""):
            selected_indices = []
        else:
            try:
                selected_indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            except ValueError:
                selected_indices = []

        installed = 0
        for idx in selected_indices:
            if 0 <= idx < len(candidates):
                rp, label = candidates[idx]
                if _install_hook(console_obj, rp, label):
                    installed += 1

        if installed == 0:
            console_obj.print(
                "  [dim]Skipped. Run 'repowise hook install --workspace' later.[/dim]"
            )
