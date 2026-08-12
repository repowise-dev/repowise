from __future__ import annotations

import inspect
import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from repowise.cli import mcp_config
from repowise.cli.commands import init_cmd, update_cmd
from repowise.cli.commands.update_cmd.command import run_update
from repowise.cli.editor_integrations import claude as claude_integration
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations import codex as codex_integration
from repowise.cli.editor_integrations.claude import ClaudeCodeSetup
from repowise.cli.editor_integrations.codex import CodexSetup
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import (
    EditorSetupOptions,
    refresh_editor_project_files,
    resolve_editor_setup_options,
    write_editor_project_files,
)
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_register_editor_clients_skipped_when_env_set(monkeypatch) -> None:
    """REPOWISE_SKIP_EDITOR_SETUP makes global client registration a no-op.

    Headless / CI / benchmark indexing (incl. transient git worktrees) must not
    mutate the developer's global editor config or repoint the single global
    'repowise' MCP entry at a path that will be deleted.
    """
    from repowise.cli.editor_setup import register_editor_clients

    registered: list[Path] = []

    class FakeIntegration:
        # ``InstallLifecycle`` declares this, and the checklist reads it to
        # decide which agents ``init`` can act on.
        integration_id = "fake"

        def write_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

        def register_client(self, c: Any, p: Path) -> None:
            registered.append(p)

        def refresh_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

    integrations = (FakeIntegration(),)

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    register_editor_clients(_silent_console(), Path("repo"), integrations=integrations)
    assert registered == []  # skipped

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    register_editor_clients(_silent_console(), Path("repo"), integrations=integrations)
    assert registered == [Path("repo")]  # runs when unset


def test_register_editor_clients_skipped_by_flag(monkeypatch) -> None:
    """--no-editor-setup skips registration with the env var unset.

    The flag is the interactive spelling of REPOWISE_SKIP_EDITOR_SETUP: a user
    who wants "index this repo, don't touch my machine" should not have to know
    about an env var.
    """
    from repowise.cli.editor_setup import register_editor_clients

    registered: list[Path] = []

    class FakeIntegration:
        # ``InstallLifecycle`` declares this, and the checklist reads it to
        # decide which agents ``init`` can act on.
        integration_id = "fake"

        def write_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

        def register_client(self, c: Any, p: Path) -> None:
            registered.append(p)

        def refresh_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

    integrations = (FakeIntegration(),)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    register_editor_clients(
        _silent_console(),
        Path("repo"),
        no_editor_setup=True,
        integrations=integrations,
    )
    assert registered == []

    # Default (flag off, env unset) still registers.
    register_editor_clients(_silent_console(), Path("repo"), integrations=integrations)
    assert registered == [Path("repo")]


def test_editor_setup_disabled_resolves_flag_or_env(monkeypatch) -> None:
    """Either source disables; neither leaves setup on."""
    from repowise.cli.editor_setup import is_editor_setup_disabled

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    assert is_editor_setup_disabled() is False
    assert is_editor_setup_disabled(True) is True

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    assert is_editor_setup_disabled() is True
    # The flag never re-enables what the env var turned off.
    assert is_editor_setup_disabled(False) is True

    # Falsy env spellings leave setup on.
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", value)
        assert is_editor_setup_disabled() is False


def _patch_distill_offer(monkeypatch: Any) -> tuple[list[str], list[bool]]:
    """Stub the rewrite hook's user-level writes; return the two call logs.

    ``install_rewrite_hook`` is stubbed rather than left live so that a
    regression in the gate fails the assertion instead of installing a real
    PreToolUse hook in the ~/.claude/settings.json of whoever runs pytest.
    """
    from repowise.cli.agent_adapters import claude_code as cc_module

    installs: list[str] = []
    verdicts: list[bool] = []

    class _Adapter:
        def detect(self) -> bool:
            return True

        def install_rewrite_hook(self) -> str:
            installs.append("installed")
            return "settings.json"

    monkeypatch.setattr(cc_module, "ClaudeCodeAdapter", _Adapter)
    monkeypatch.setattr(
        "repowise.cli.helpers.save_distill_commands_enabled",
        lambda _path, *, enabled: verdicts.append(enabled),
    )
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    return installs, verdicts


def test_distill_rewrite_install_skipped_by_flag(monkeypatch, tmp_path: Path) -> None:
    """--no-editor-setup suppresses the install, which is user-level.

    ``--distill-hook`` would otherwise install without prompting. Nothing is
    recorded: with no hook installed there is nothing for a verdict to gate,
    and "enabled" is already the posture of any repo with a `.repowise/`.
    """
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

    installs, verdicts = _patch_distill_offer(monkeypatch)

    console = _silent_console()
    offer_distill_rewrite_hook(console, [tmp_path], True, yes=True, no_editor_setup=True)
    assert installs == []
    assert verdicts == []
    # The user asked for the hook and did not get it; say so.
    assert "not installed" in console.file.getvalue()


def test_distill_optout_recorded_despite_no_editor_setup(monkeypatch, tmp_path: Path) -> None:
    """--no-editor-setup must not swallow --no-distill-hook's opt-out record.

    The verdict lives in this repo's config.yaml, and it is the only thing that
    gates a *globally* installed rewrite hook off for this repo. Dropping the
    write would leave the hook rewriting commands in a repo the user just
    opted out of.
    """
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

    installs, verdicts = _patch_distill_offer(monkeypatch)

    offer_distill_rewrite_hook(
        _silent_console(),
        [tmp_path],
        False,
        yes=True,
        no_editor_setup=True,
    )
    assert installs == []
    assert verdicts == [False]


def test_the_consent_names_every_surface_a_yes_turns_on(monkeypatch, tmp_path: Path) -> None:
    """One question, but it has to say what it covers.

    A yes here writes every ``hooks.<surface>`` key, so a surface added without
    a line in this prompt is one the user enabled without being told. That is
    not a hypothetical: read-skeleton shipped undisclosed, and search-digest
    was added to the same consent while the prompt still named only Reads.
    The `repowise hook <name>` toggle stands in for the surface, since it is
    the string a user can act on.
    """
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook
    from repowise.cli.helpers import HOOK_REPLACEMENT_SURFACES

    _patch_distill_offer(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("click.confirm", lambda *a, **k: False)

    console = _silent_console()
    offer_distill_rewrite_hook(console, [tmp_path], None)
    # Collapsed: rich wraps to the console width, so a phrase this asserts on
    # can arrive split across two lines.
    shown = " ".join(console.file.getvalue().split())

    for surface in HOOK_REPLACEMENT_SURFACES:
        toggle = f"repowise hook {surface.replace('_', '-')}"
        assert toggle in shown, f"the consent prompt never mentions {toggle}"


def test_the_distill_hook_flag_names_every_surface_it_decides() -> None:
    """``--distill-hook`` sets all of them with no prompt at all, so its help
    text is the only disclosure that path has."""
    from repowise.cli.commands.init_cmd.command import init_command
    from repowise.cli.helpers import HOOK_REPLACEMENT_SURFACES

    (option,) = [o for o in init_command.params if o.name == "distill_hook"]
    for surface in HOOK_REPLACEMENT_SURFACES:
        assert surface in option.help, f"--distill-hook help never mentions {surface}"


def _hook_verdicts(repo_path: Path) -> tuple[object, ...]:
    """``distill.commands.enabled`` then every ``hooks.<surface>``, from disk.

    Built from ``HOOK_REPLACEMENT_SURFACES`` rather than a literal list, so a
    surface added without a writer fails these tests instead of shipping
    unreachable, which is exactly how read-skeleton shipped.
    """
    import yaml

    from repowise.cli.helpers import HOOK_REPLACEMENT_SURFACES

    cfg = yaml.safe_load((repo_path / ".repowise" / "config.yaml").read_text("utf-8")) or {}
    hooks = cfg.get("hooks") or {}
    return (
        ((cfg.get("distill") or {}).get("commands") or {}).get("enabled"),
        *(hooks.get(surface) for surface in HOOK_REPLACEMENT_SURFACES),
    )


def _all_same(verdicts: tuple[object, ...], answer: bool) -> bool:
    return verdicts == (answer,) * len(verdicts)


@pytest.mark.parametrize("answer", [True, False])
def test_the_rewrite_hook_answer_decides_every_replacing_surface(
    monkeypatch, tmp_path: Path, answer: bool
) -> None:
    """One question, every key.

    The prompt already means "repowise's hooks may intervene in my agent's
    tool calls", and rewriting a Bash command into `repowise distill` is a
    larger intervention than serving a Read as its skeleton or a search as its
    digest, not a smaller one, so a second prompt would be asking for
    permission already given.

    The concrete thing this pins is that each replacing surface has *a* writer
    at all. Read-skeleton shipped with none, reachable only by hand-editing
    YAML, which left its gate needing 50 firings it could never collect.
    """
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(
        "repowise.cli.agent_adapters.claude_code.ClaudeCodeAdapter.install_rewrite_hook",
        lambda self: tmp_path / "settings.json",
    )

    offer_distill_rewrite_hook(_silent_console(), [tmp_path], answer, yes=True)

    assert _all_same(_hook_verdicts(tmp_path), answer)


def test_no_editor_setup_turns_off_every_replacing_surface(monkeypatch, tmp_path: Path) -> None:
    """One flag, one meaning: no hooks. An opt-out that left one of these on
    would be `--no-editor-setup` still letting a hook rewrite what a tool
    returns."""
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

    (tmp_path / ".repowise").mkdir()

    offer_distill_rewrite_hook(
        _silent_console(), [tmp_path], False, yes=True, no_editor_setup=True
    )

    assert _all_same(_hook_verdicts(tmp_path), False)


def test_distill_offer_silent_when_undecided_and_setup_off(monkeypatch, tmp_path: Path) -> None:
    """No flag plus no editor setup means nothing to install and nothing to ask."""
    from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook

    installs, verdicts = _patch_distill_offer(monkeypatch)

    offer_distill_rewrite_hook(
        _silent_console(),
        [tmp_path],
        None,
        no_editor_setup=True,
    )
    assert installs == []
    assert verdicts == []


def test_detect_editor_setup_outcome_flag_reports_disconnected(tmp_path: Path, monkeypatch) -> None:
    """The completion panel reacts to the flag, not just the env var."""
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    outcome = detect_editor_setup_outcome(
        tmp_path,
        interactive=False,
        first_index=False,
        no_editor_setup=True,
    )
    assert outcome.editor_setup_disabled is True
    assert outcome.claude_code_connected is False


def test_detect_editor_setup_outcome_reads_ground_truth(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """The snapshot reflects the real hook state and the skip-setup env flag."""
    from repowise.cli import hooks as hooks_module
    from repowise.cli.agent_adapters import claude_code as cc_module
    from repowise.cli.agent_adapters import codex as codex_module
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    monkeypatch.setattr(hooks_module, "status", lambda _p: "installed")

    class _Adapter:
        def __init__(self, installed: bool, detected: bool = True) -> None:
            self._installed = installed
            self._detected = detected

        def detect(self) -> bool:
            return self._detected

        def rewrite_hook_installed(self) -> bool:
            return self._installed

    monkeypatch.setattr(cc_module, "ClaudeCodeAdapter", lambda: _Adapter(installed=False))
    # Codex is detected and has the rewrite hook, so it counts as present.
    monkeypatch.setattr(codex_module, "CodexAdapter", lambda: _Adapter(installed=True))
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    outcome = detect_editor_setup_outcome(tmp_path, interactive=True, first_index=True)
    assert outcome.claude_code_connected is True
    assert outcome.editor_setup_disabled is False
    assert outcome.autosync_hook_installed is True
    assert outcome.rewrite_hook_installed is True  # Codex surface has it
    assert outcome.interactive is True
    assert outcome.first_index is True


def test_detect_editor_setup_outcome_skip_env_reports_disconnected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    outcome = detect_editor_setup_outcome(tmp_path, interactive=False, first_index=False)
    assert outcome.editor_setup_disabled is True
    assert outcome.claude_code_connected is False


def test_resolve_editor_setup_options_carries_the_cli_flags_through() -> None:
    """Just the flags now.

    This used to give every integration a ``configure_options`` hook to prompt
    from. The prompting is one registry-built checklist
    (:func:`select_agents_interactively`), so the hook had no implementation
    left that did anything and all three were deleted with it.
    """
    options = resolve_editor_setup_options(
        disabled_project_files={"cli_disabled"},
        project_file_overrides={"agents_md": False},
        integration_overrides={"codex": True},
    )

    assert options.disabled_project_files == frozenset({"cli_disabled"})
    assert options.project_file_overrides == {"agents_md": False}
    assert options.integration_overrides == {"codex": True}


# ---------------------------------------------------------------------------
# The agent checklist that replaced the three per-integration prompts
# ---------------------------------------------------------------------------


def _select(monkeypatch, tmp_path: Path, answer, **kwargs):
    """Run the checklist with *answer* standing in for the user's reply."""
    from repowise.cli.editor_setup import select_agents_interactively
    from repowise.cli.ui import agent_selection

    seen: list[list] = []

    def _fake(console_obj, choices):
        seen.append(choices)
        return answer(choices) if callable(answer) else answer

    monkeypatch.setattr(agent_selection, "interactive_agent_select", _fake)
    options = select_agents_interactively(
        _silent_console(), tmp_path, EditorSetupOptions(**kwargs)
    )
    return options, seen[0]


def test_checklist_unticking_an_agent_disables_its_project_file(monkeypatch, tmp_path) -> None:
    """What the three deleted yes/no prompts each did, now in one place."""
    options, _ = _select(monkeypatch, tmp_path, {"codex"})

    assert "claude_md" in options.disabled_project_files
    assert "vscode_mcp" in options.disabled_project_files
    assert "agents_md" not in options.disabled_project_files
    assert options.integration_overrides["codex"] is True


def test_checklist_ticking_everything_disables_nothing(monkeypatch, tmp_path) -> None:
    options, _ = _select(
        monkeypatch, tmp_path, lambda choices: {choice.id for choice in choices}
    )

    from repowise.cli.editor_integrations.defaults import get_default_editor_integrations

    assert options.disabled_project_files == frozenset()
    # Derived from the *setup integrations*, not the target registry, and the
    # difference is the point: the checklist offers what ``init`` can write, and
    # a registered agent without a setup integration is not that. Restating the
    # list here would make a fifth agent fail two tests for one reason.
    assert options.integration_overrides == {
        integration.integration_id: True for integration in get_default_editor_integrations()
    }


def test_checklist_offers_only_agents_init_can_write(monkeypatch, tmp_path) -> None:
    """A ticked box that writes nothing reads as success, because the others print a line.

    The checklist is built from the agent registry and the writing is done by
    the setup integrations, and those are two lists. An agent can be registered
    (matrix row, ``--target`` id, ``doctor`` row) without ``init`` having a
    writer for it, and Cursor is the first one that is. It gets a pointer to the
    command that does wire it, not a box that quietly does nothing.
    """
    from repowise.cli.editor_integrations.defaults import get_default_editor_integrations

    _, choices = _select(monkeypatch, tmp_path, lambda c: {choice.id for choice in c})

    offered = {choice.id for choice in choices}
    assert offered == {i.integration_id for i in get_default_editor_integrations()}
    assert "cursor" not in offered


def test_checklist_pre_ticks_an_agent_an_explicit_flag_asked_for(monkeypatch, tmp_path) -> None:
    """``--codex`` is already an answer, so the box it controls starts ticked.

    Otherwise accepting the checklist would silently undo the flag the user
    passed on the same command line.
    """
    _, choices = _select(
        monkeypatch, tmp_path, set(), integration_overrides={"codex": True}
    )

    codex = next(choice for choice in choices if choice.id == "codex")
    assert codex.enabled is True


def test_checklist_never_silently_withdraws_the_instruction_file_default(
    monkeypatch, tmp_path
) -> None:
    """Leaving a box unticked is not neutral, so detection must not do it alone.

    On a machine with Codex but no ``~/.claude``, detection returned a
    non-empty selection, so the auto fallback never fired and Claude Code
    arrived unticked. One Enter then persisted ``claude_md: false`` into
    ``.repowise/config.yaml``, so ``update`` never generated it either — where
    the prompt this replaced defaulted to yes.

    The first fix for this unioned in ``resolve_target_flag("auto")``, which
    *looks* equivalent and is a no-op: ``auto`` resolves to the detected
    targets and only reaches the fallback when detection is empty — the case
    the union already covered. So the moment anything else was wired, Claude
    Code went missing again. Hence the wired row below.
    """
    from repowise.cli.agent_targets.registry import default_selection

    rows = [
        {"id": "claude-code", "registrations": [], "present": False},
        {"id": "codex", "registrations": [{"method": "direct"}], "present": True},
        {"id": "vscode", "registrations": [], "present": True},
    ]

    assert default_selection(rows) == {"claude-code", "codex", "vscode"}


def test_checklist_that_cannot_be_answered_leaves_the_options_alone(monkeypatch, tmp_path) -> None:
    """isatty lies. A prompt returning None must not disable everything."""
    options, _ = _select(monkeypatch, tmp_path, None)

    assert options.disabled_project_files == frozenset()
    assert options.integration_overrides == {}


def test_default_disabled_project_files_maps_legacy_no_claude_flag() -> None:
    assert get_default_disabled_project_files() == ()
    assert get_default_disabled_project_files(no_claude_md=True) == ("claude_md",)


def test_default_overrides_map_codex_flags_to_integration_ids() -> None:
    assert get_default_project_file_overrides() == {}
    assert get_default_project_file_overrides(agents_md=False) == {"agents_md": False}
    assert get_default_integration_overrides() == {}
    assert get_default_integration_overrides(codex_setup=True) == {"codex": True}


def test_write_editor_project_files_saves_common_mcp_before_integrations(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, object, object | None]] = []

    def fake_save_mcp_config(repo_path: Path) -> Path:
        calls.append(("mcp", repo_path, None))
        return repo_path / ".repowise" / "mcp.json"

    class FakeIntegration:
        # ``InstallLifecycle`` declares this, and the checklist reads it to
        # decide which agents ``init`` can act on.
        integration_id = "fake"

        def write_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            options: EditorSetupOptions,
        ) -> None:
            calls.append(("fake-project", repo_path, options.disabled_project_files))

        def register_client(self, console_obj: object, repo_path: Path) -> None:
            raise AssertionError("not used")

    monkeypatch.setattr(mcp_config, "save_mcp_config", fake_save_mcp_config)

    write_editor_project_files(
        _silent_console(),
        tmp_path,
        disabled_project_files={"fake_instructions"},
        integrations=(FakeIntegration(),),
    )

    assert calls == [
        ("mcp", tmp_path, None),
        ("fake-project", tmp_path, frozenset({"fake_instructions"})),
    ]


def test_write_editor_project_files_uses_pre_resolved_options(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path, EditorSetupOptions]] = []
    options = EditorSetupOptions(
        disabled_project_files=frozenset({"resolved"}),
        integration_overrides={"codex": True},
    )

    def fake_save_mcp_config(repo_path: Path) -> Path:
        calls.append(("mcp", repo_path, options))
        return repo_path / ".repowise" / "mcp.json"

    class FakeIntegration:
        # ``InstallLifecycle`` declares this, and the checklist reads it to
        # decide which agents ``init`` can act on.
        integration_id = "fake"

        def write_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            received_options: EditorSetupOptions,
        ) -> None:
            calls.append(("fake-project", repo_path, received_options))

    monkeypatch.setattr(mcp_config, "save_mcp_config", fake_save_mcp_config)

    write_editor_project_files(
        _silent_console(),
        tmp_path,
        options=options,
        disabled_project_files={"ignored"},
        integrations=(FakeIntegration(),),  # type: ignore[arg-type]
    )

    assert calls == [
        ("mcp", tmp_path, options),
        ("fake-project", tmp_path, options),
    ]


def test_claude_project_setup_writes_root_mcp_and_claude_md(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, object, object | None]] = []

    def fake_save_root_mcp_config(repo_path: Path) -> Path:
        calls.append(("root-mcp", repo_path, None))
        return repo_path / ".mcp.json"

    def fake_maybe_generate_claude_md(
        console_obj: object,
        repo_path: Path,
        *,
        no_claude_md: bool = False,
    ) -> None:
        calls.append(("claude-md", repo_path, no_claude_md))

    monkeypatch.setattr(mcp_config, "save_root_mcp_config", fake_save_root_mcp_config)
    monkeypatch.setattr(
        claude_integration,
        "maybe_generate_claude_md",
        fake_maybe_generate_claude_md,
    )

    ClaudeCodeSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"claude_md"})),
    )

    assert calls == [
        ("root-mcp", tmp_path, None),
        ("claude-md", tmp_path, True),
    ]


def test_refresh_editor_project_files_delegates_to_integrations(tmp_path: Path) -> None:
    calls: list[tuple[str, Path, frozenset[str]]] = []

    class FakeIntegration:
        # ``InstallLifecycle`` declares this, and the checklist reads it to
        # decide which agents ``init`` can act on.
        integration_id = "fake"

        def refresh_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            options: EditorSetupOptions,
        ) -> None:
            calls.append(("refresh", repo_path, options.disabled_project_files))

    refresh_editor_project_files(
        _silent_console(),
        tmp_path,
        options=EditorSetupOptions(disabled_project_files=frozenset({"skip"})),
        integrations=(FakeIntegration(),),  # type: ignore[arg-type]
    )

    assert calls == [("refresh", tmp_path, frozenset({"skip"}))]


def _write_settings(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"repowise": entry}}), encoding="utf-8")


def test_describe_mcp_registration_change_warns_on_repoint(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A second init from another repo replaces the single 'repowise' entry.

    That write is silent and only surfaces later, when the repo or binary it
    points at is gone. The probe names both sides before the merge happens.
    """
    settings = tmp_path / "home" / ".claude" / "settings.json"
    other_repo = str((tmp_path / "other-repo").resolve()).replace("\\", "/")
    _write_settings(
        settings,
        {"command": "repowise", "args": ["mcp", other_repo, "--transport", "stdio"]},
    )

    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "repowise")

    this_repo = tmp_path / "this-repo"
    this_repo.mkdir()
    notice = claude_config.describe_mcp_registration_change(this_repo)

    assert notice is not None
    assert other_repo in notice
    assert str(this_repo.resolve()).replace("\\", "/") in notice
    assert "--no-editor-setup" in notice


def test_describe_mcp_registration_change_silent_when_unchanged(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Re-running init on the same repo is not a clobber and stays quiet."""
    settings = tmp_path / "home" / ".claude" / "settings.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_arg = str(repo.resolve()).replace("\\", "/")
    _write_settings(
        settings,
        {"command": "repowise", "args": ["mcp", repo_arg, "--transport", "stdio"]},
    )

    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "repowise")

    assert claude_config.describe_mcp_registration_change(repo) is None


def test_describe_mcp_registration_change_silent_on_first_run(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """No stored entry means nothing is being replaced."""
    monkeypatch.setattr(
        claude_config, "_claude_code_settings_path", lambda: tmp_path / "missing.json"
    )
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "repowise")

    repo = tmp_path / "repo"
    repo.mkdir()
    assert claude_config.describe_mcp_registration_change(repo) is None


def test_describe_mcp_registration_change_warns_on_command_repoint(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Same repo, different binary — the release-smoke-test case."""
    settings = tmp_path / "home" / ".claude" / "settings.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_arg = str(repo.resolve()).replace("\\", "/")
    _write_settings(
        settings,
        {
            "command": "C:/Users/dev/.venv/Scripts/repowise.exe",
            "args": ["mcp", repo_arg, "--transport", "stdio"],
        },
    )

    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(
        claude_config, "resolve_repowise_command", lambda: "C:/tmp/throwaway/repowise.exe"
    )

    notice = claude_config.describe_mcp_registration_change(repo)
    assert notice is not None
    assert "C:/Users/dev/.venv/Scripts/repowise.exe" in notice
    assert "C:/tmp/throwaway/repowise.exe" in notice


def test_describe_mcp_registration_change_reports_both_fields(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A new repo indexed by a new install moves both fields; name both."""
    settings = tmp_path / "home" / ".claude" / "settings.json"
    other = str((tmp_path / "other").resolve()).replace("\\", "/")
    _write_settings(
        settings,
        {"command": "old-repowise", "args": ["mcp", other, "--transport", "stdio"]},
    )

    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "new-repowise")

    repo = tmp_path / "repo"
    repo.mkdir()
    notice = claude_config.describe_mcp_registration_change(repo)

    assert notice is not None
    assert "repo and command" in notice
    assert "old-repowise" in notice and "new-repowise" in notice
    assert other in notice


def test_describe_mcp_registration_change_handles_truncated_entry(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A hand-edited entry with no args must not print 'was: None'."""
    settings = tmp_path / "home" / ".claude" / "settings.json"
    _write_settings(settings, {"command": "repowise", "args": ["mcp"]})

    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)
    monkeypatch.setattr(claude_config, "_claude_desktop_config_path", lambda: None)
    monkeypatch.setattr(claude_config, "resolve_repowise_command", lambda: "repowise")

    repo = tmp_path / "repo"
    repo.mkdir()
    notice = claude_config.describe_mcp_registration_change(repo)

    assert notice is not None
    assert "None" not in notice
    assert "(not set)" in notice


def test_codex_project_setup_writes_project_config_hooks_and_agents(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path, bool | None]] = []

    monkeypatch.setattr(mcp_config, "is_codex_cli_installed", lambda: True)
    monkeypatch.setattr(mcp_config, "is_codex_logged_in", lambda: True)
    monkeypatch.setattr(
        mcp_config,
        "save_codex_mcp_config",
        lambda repo_path: (
            calls.append(("codex-mcp", repo_path, None)) or repo_path / ".codex" / "config.toml"
        ),
    )
    monkeypatch.setattr(
        mcp_config,
        "save_codex_hooks_config",
        lambda repo_path: (
            calls.append(("codex-hooks", repo_path, None)) or repo_path / ".codex" / "hooks.json"
        ),
    )
    monkeypatch.setattr(
        codex_integration,
        "maybe_generate_agents_md",
        lambda _console, repo_path, *, agents_md=None: calls.append(
            ("agents-md", repo_path, agents_md)
        ),
    )

    CodexSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(
            integration_overrides={"codex": True},
            project_file_overrides={"agents_md": False},
        ),
    )

    assert calls == [
        ("codex-mcp", tmp_path, None),
        ("codex-hooks", tmp_path, None),
        ("agents-md", tmp_path, False),
    ]


def test_codex_project_setup_enables_agents_by_default_with_codex(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path, bool | None]] = []

    monkeypatch.setattr(mcp_config, "is_codex_cli_installed", lambda: True)
    monkeypatch.setattr(mcp_config, "is_codex_logged_in", lambda: True)
    monkeypatch.setattr(
        mcp_config,
        "save_codex_mcp_config",
        lambda repo_path: (
            calls.append(("codex-mcp", repo_path, None)) or repo_path / ".codex" / "config.toml"
        ),
    )
    monkeypatch.setattr(
        mcp_config,
        "save_codex_hooks_config",
        lambda repo_path: (
            calls.append(("codex-hooks", repo_path, None)) or repo_path / ".codex" / "hooks.json"
        ),
    )
    monkeypatch.setattr(
        codex_integration,
        "maybe_generate_agents_md",
        lambda _console, repo_path, *, agents_md=None, default=True: calls.append(
            ("agents-md", repo_path, agents_md)
        ),
    )

    CodexSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(integration_overrides={"codex": True}),
    )

    assert calls == [
        ("codex-mcp", tmp_path, None),
        ("codex-hooks", tmp_path, None),
        ("agents-md", tmp_path, True),
    ]


def test_codex_project_setup_skips_when_disabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        mcp_config,
        "save_codex_mcp_config",
        lambda _repo_path: calls.append("codex-mcp"),
    )

    CodexSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(integration_overrides={"codex": False}),
    )

    assert calls == []


def test_codex_project_setup_skips_without_explicit_opt_in(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(mcp_config, "is_codex_cli_installed", lambda: True)
    monkeypatch.setattr(mcp_config, "is_codex_logged_in", lambda: True)
    monkeypatch.setattr(
        mcp_config,
        "save_codex_mcp_config",
        lambda _repo_path: calls.append("codex-mcp"),
    )

    CodexSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(),
    )

    assert calls == []


def test_codex_project_setup_writes_agents_without_codex_setup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path, bool | None]] = []

    monkeypatch.setattr(
        mcp_config,
        "save_codex_mcp_config",
        lambda _repo_path: calls.append(("unexpected-codex-mcp", tmp_path, None)),
    )
    monkeypatch.setattr(
        mcp_config,
        "save_codex_hooks_config",
        lambda _repo_path: calls.append(("unexpected-codex-hooks", tmp_path, None)),
    )
    monkeypatch.setattr(
        codex_integration,
        "maybe_generate_agents_md",
        lambda _console, repo_path, *, agents_md=None: calls.append(
            ("agents-md", repo_path, agents_md)
        ),
    )

    CodexSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(project_file_overrides={"agents_md": True}),
    )

    assert calls == [("agents-md", tmp_path, True)]


def test_codex_refresh_project_files_delegates_to_agents_generator(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[Path, bool | None, bool]] = []

    monkeypatch.setattr(
        codex_integration,
        "maybe_generate_agents_md",
        lambda _console, repo_path, *, agents_md=None, default=True: calls.append(
            (repo_path, agents_md, default)
        ),
    )

    CodexSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(project_file_overrides={"agents_md": True}),
    )

    assert calls == [(tmp_path, True, False)]


def test_claude_refresh_project_files_writes_when_enabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(),
    )

    assert calls == [tmp_path]


def test_claude_refresh_project_files_skips_when_config_disabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "editor_files:\n  claude_md: false\n",
        encoding="utf-8",
    )

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(),
    )

    assert calls == []


def test_claude_refresh_project_files_skips_when_options_disable_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"claude_md"})),
    )

    assert calls == []


def test_update_command_uses_editor_refresh_abstraction() -> None:
    from repowise.cli.commands.update_cmd.command import _refresh_editor_stamp

    command_source = inspect.getsource(run_update)
    stamp_source = inspect.getsource(_refresh_editor_stamp)

    # The command routes every editor-file write through the shared stamp
    # helper, which in turn uses the refresh abstraction — never the raw
    # generator/fetcher internals.
    assert "_refresh_editor_stamp" in command_source
    assert "refresh_editor_project_files" in stamp_source
    for source in (command_source, stamp_source):
        assert "ClaudeMdGenerator" not in source
        assert "EditorFileDataFetcher" not in source
        assert "claude_md" not in source


def test_workspace_update_refreshes_agents_for_selected_repos(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    (backend / ".repowise").mkdir(parents=True)
    (frontend / ".repowise").mkdir(parents=True)
    ws_config = WorkspaceConfig(
        repos=[
            RepoEntry(path="backend", alias="backend"),
            RepoEntry(path="frontend", alias="frontend"),
        ],
    )
    calls: list[tuple[Path, dict[str, bool]]] = []

    def fake_refresh(_console: object, repo_path: Path, *, options: EditorSetupOptions):
        calls.append((repo_path, options.project_file_overrides))

    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files",
        fake_refresh,
    )

    update_cmd._refresh_workspace_editor_project_files(
        ws_root=tmp_path,
        ws_config=ws_config,
        repo_filter=None,
        agents_md=True,
    )

    assert calls == [
        (backend.resolve(), {"agents_md": True}),
        (frontend.resolve(), {"agents_md": True}),
    ]


def test_workspace_generation_rebinds_codex_provider_to_repo(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeProvider:
        provider_name = "codex_cli"
        model_name = "codex_cli/gpt-5.5"

    rebound = object()
    calls: list[tuple[str, str, Path]] = []

    def fake_resolve_provider(provider_name: str, model: str, repo_path: Path) -> object:
        calls.append((provider_name, model, repo_path))
        return rebound

    monkeypatch.setattr(init_cmd.workspace, "resolve_provider", fake_resolve_provider)

    result = init_cmd.workspace._workspace_generation_provider_for_repo(FakeProvider(), tmp_path)

    assert result is rebound
    assert calls == [("codex_cli", "codex_cli/gpt-5.5", tmp_path)]


def test_init_command_uses_editor_option_abstraction() -> None:
    source = inspect.getsource(init_cmd.init_command.callback) + inspect.getsource(
        init_cmd._workspace_init
    )

    assert "resolve_editor_setup_options" in source
    assert "write_editor_project_files" in source
    assert "interactive_claude_md_prompt" not in source
    assert 'disabled_project_files={"claude_md"}' not in source


def test_claude_client_registration_uses_existing_claude_setup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_desktop(repo_path: Path) -> Path:
        calls.append(("desktop", repo_path))
        return tmp_path / "claude_desktop_config.json"

    def fake_code(repo_path: Path) -> Path:
        calls.append(("code", repo_path))
        return tmp_path / ".claude" / "settings.json"

    def fake_hooks() -> Path:
        calls.append(("hooks", tmp_path))
        return tmp_path / ".claude" / "settings.json"

    def fake_tool_search() -> Path:
        calls.append(("tool_search", tmp_path))
        return tmp_path / ".claude" / "settings.json"

    monkeypatch.setattr(claude_config, "register_with_claude_desktop", fake_desktop)
    monkeypatch.setattr(claude_config, "register_with_claude_code", fake_code)
    monkeypatch.setattr(claude_config, "install_claude_code_hooks", fake_hooks)
    monkeypatch.setattr(claude_config, "enable_tool_search_in_claude_code", fake_tool_search)

    output = StringIO()
    console = Console(file=output, force_terminal=False)

    ClaudeCodeSetup().register_client(console, tmp_path)

    assert calls == [
        ("desktop", tmp_path),
        ("code", tmp_path),
        ("hooks", tmp_path),
        ("tool_search", tmp_path),
    ]
    text = output.getvalue()
    assert "Claude Desktop MCP registered" in text
    assert "Claude Code MCP registered" in text
    assert "Claude Code hooks registered" in text
    assert "tool-search enabled" in text


def test_lean_tool_surface_skips_tool_search_recommendation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A repo configured with the lean profile keeps schemas always loaded."""
    calls: list[str] = []
    monkeypatch.setattr(claude_config, "register_with_claude_desktop", lambda p: None)
    monkeypatch.setattr(claude_config, "register_with_claude_code", lambda p: None)
    monkeypatch.setattr(claude_config, "install_claude_code_hooks", lambda: None)
    monkeypatch.setattr(
        claude_config,
        "enable_tool_search_in_claude_code",
        lambda: calls.append("tool_search"),
    )

    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text("mcp:\n  tools: lean\n", encoding="utf-8")

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    ClaudeCodeSetup().register_client(console, tmp_path)

    assert calls == []
    assert "Lean MCP tool surface configured" in output.getvalue()


def test_uses_lean_tool_surface_shapes(tmp_path: Path) -> None:
    from repowise.cli.editor_integrations.claude import _uses_lean_tool_surface

    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir()

    assert _uses_lean_tool_surface(tmp_path) is False  # no config

    for text, expected in [
        ("mcp:\n  tools: lean\n", True),
        ("mcp:\n  tools: LEAN\n", True),
        ("mcp:\n  tools: [lean]\n", True),
        ("mcp:\n  tools: ['+get_execution_flows']\n", False),
        ("mcp:\n  tools: all\n", False),
        ("mcp:\n  tools: [lean, get_health]\n", False),
    ]:
        config.write_text(text, encoding="utf-8")
        assert _uses_lean_tool_surface(tmp_path) is expected, text


def test_enable_tool_search_sets_env_idempotently(tmp_path: Path, monkeypatch: Any) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)

    # First call creates the env block and sets the flag.
    assert claude_config.enable_tool_search_in_claude_code() == settings
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["env"]["ENABLE_TOOL_SEARCH"] == "true"

    # Idempotent: a second call leaves it set but reports nothing new to do.
    assert claude_config.enable_tool_search_in_claude_code() is None

    # A user's explicit value is never overwritten.
    settings.write_text(json.dumps({"env": {"ENABLE_TOOL_SEARCH": "false"}}), encoding="utf-8")
    assert claude_config.enable_tool_search_in_claude_code() is None
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["env"]["ENABLE_TOOL_SEARCH"] == "false"


def test_enable_tool_search_preserves_existing_settings(tmp_path: Path, monkeypatch: Any) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"env": {"FOO": "bar"}, "mcpServers": {"repowise": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: settings)

    assert claude_config.enable_tool_search_in_claude_code() == settings
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["env"] == {"FOO": "bar", "ENABLE_TOOL_SEARCH": "true"}
    assert data["mcpServers"] == {"repowise": {}}
