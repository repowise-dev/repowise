"""Three drift leaks that were silent, and the machinery that makes them speak.

Grouped because they share one shape: something the CLI wrote, or something the
CLI cannot write, goes out of date and nothing says so. Silence is the failure
mode in each case, so every test here asserts on an *observable* — a doctor
issue, a file on disk, a call count — and never on "the function returned".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli import __version__
from repowise.cli.agent_targets.targets import claude_code, codex
from repowise.cli.agent_targets.types import FileAction, Scope


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    """A redirected home, per the standing trap.

    ``HOMEDRIVE``/``HOMEPATH`` go with ``USERPROFILE`` because both Claude config
    paths derive from ``Path.home()``, and ``Path.home`` itself is patched so a
    module that already resolved it cannot reach the real one.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive) :])
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    return home


# ---------------------------------------------------------------------------
# Leak 1 (installed half): Codex slash commands
# ---------------------------------------------------------------------------


def test_codex_prompts_are_installed_into_the_only_place_codex_reads(fake_home) -> None:
    written = codex.write_prompts()

    bundled = dict(codex.bundled_prompts())
    assert bundled, "no Codex prompts are bundled in the wheel"
    assert {w.path.name for w in written} == set(bundled)
    assert all(w.action is FileAction.CREATED for w in written)

    # Asserted on the generated files, not the return value: a renderer can be
    # perfectly correct about text nobody wrote to disk.
    for name, text in bundled.items():
        landed = codex.user_prompts_dir() / name
        assert landed.read_text(encoding="utf-8").replace("\r\n", "\n") == text
        assert name.startswith(codex.PROMPT_PREFIX), (
            "~/.codex/prompts is a flat global directory shared with every other "
            "tool the user has installed"
        )


def test_reinstalling_prompts_reports_unchanged_rather_than_rewriting(fake_home) -> None:
    codex.write_prompts()
    again = codex.write_prompts()

    assert {w.action for w in again} == {FileAction.UNCHANGED}


def test_a_hand_edited_prompt_is_restored(fake_home) -> None:
    codex.write_prompts()
    edited = codex.user_prompts_dir() / "repowise-risk.md"
    edited.write_text("clobbered", encoding="utf-8")

    actions = {w.path.name: w.action for w in codex.write_prompts()}

    assert actions["repowise-risk.md"] is FileAction.UPDATED
    assert "clobbered" not in edited.read_text(encoding="utf-8")


def test_uninstall_takes_prompts_a_previous_release_left_behind(fake_home) -> None:
    """Matched on the prefix, not on the bundled set.

    A command removed from ``plugins/shared/commands/`` still has a file sitting
    in the user's global prompts directory from whenever they last installed. It
    is ours, and leaving it is how a shared global directory silently grows.
    """
    codex.write_prompts()
    orphan = codex.user_prompts_dir() / "repowise-retired-command.md"
    orphan.write_text("from an older release\n", encoding="utf-8")
    stranger = codex.user_prompts_dir() / "someone-elses.md"
    stranger.write_text("not ours\n", encoding="utf-8")

    codex.TARGET.uninstall(Scope.USER)

    assert not orphan.exists()
    assert not list(codex.user_prompts_dir().glob("repowise-*.md"))
    assert stranger.exists(), "uninstall reached into another tool's prompt"


def test_the_prompts_directory_is_named_among_the_paths_codex_owns(fake_home) -> None:
    """``agents remove`` promising a path it does not clear was a real defect."""
    paths = codex.TARGET.describe_paths(Scope.USER)

    assert str(codex.user_prompts_dir()) in paths


# ---------------------------------------------------------------------------
# Leak 2: plugin version skew
# ---------------------------------------------------------------------------


def _install_plugin(home: Path, version: str) -> None:
    """Write the host's own manifest, schema version 2."""
    manifest = home / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    claude_code.PLUGIN_KEY: [
                        {
                            "scope": "user",
                            "installPath": str(home / ".claude" / "plugins" / "repowise"),
                            "version": version,
                            "installedAt": "2026-06-03T00:00:00Z",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_a_plugin_at_the_cli_version_is_not_skew(fake_home) -> None:
    _install_plugin(fake_home, __version__)

    assert claude_code.plugin_version_skew() == []
    assert claude_code.TARGET.doctor().status.value == "ok"


def test_a_stale_plugin_is_reported_with_the_host_command_that_fixes_it(fake_home) -> None:
    """The measured case: 0.16.0 installed months earlier against a 0.41.0 CLI."""
    _install_plugin(fake_home, "0.16.0")

    report = claude_code.TARGET.doctor()

    assert report.status.value == "stale"
    assert claude_code.plugin_version_skew() == ["0.16.0"]
    skew = [issue for issue in report.issues if "0.16.0" in issue]
    assert skew, report.issues
    assert __version__ in skew[0]
    assert "pip install -U repowise" in skew[0], (
        "the point of the message is that upgrading the CLI does not do this"
    )
    assert report.fix_command == claude_code.PLUGIN_UPDATE_COMMAND


def test_a_plugin_ahead_of_the_cli_sends_the_user_to_pip_instead(fake_home) -> None:
    """The same drift running the other way.

    Telling someone to update an already-newer plugin is a dead end, and it is a
    real state: the host updates plugins on its own schedule.
    """
    _install_plugin(fake_home, "99.0.0")

    report = claude_code.TARGET.doctor()

    assert report.status.value == "stale"
    assert report.fix_command == "pip install -U repowise"


def test_an_install_record_with_no_version_is_not_reported_as_skew(fake_home) -> None:
    """Unactionable, so silent — the same degradation `_plugin_installs` makes."""
    manifest = fake_home / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"version": 2, "plugins": {claude_code.PLUGIN_KEY: [{"scope": "user"}]}}),
        encoding="utf-8",
    )

    assert claude_code.plugin_version_skew() == []


# ---------------------------------------------------------------------------
# Leak 3: the self-heal version stamp
# ---------------------------------------------------------------------------


@pytest.fixture
def counted_migrations(monkeypatch) -> dict[str, int]:
    """Both migrations, replaced by counters.

    Patched on the modules they live in rather than on ``self_heal``, because
    ``self_heal`` imports them inside the function — patching a name it has not
    bound yet would silently test nothing.
    """
    from repowise.cli.editor_integrations import claude_config, codex_config

    calls = {"claude": 0, "codex": 0}

    def claude() -> bool:
        calls["claude"] += 1
        return False

    def codex_migration() -> bool:
        calls["codex"] += 1
        return False

    monkeypatch.setattr(claude_config, "migrate_claude_code_hooks", claude)
    monkeypatch.setattr(codex_config, "migrate_codex_rewrite_hook", codex_migration)
    return calls


def test_the_migrations_run_once_per_version_not_once_per_invocation(
    fake_home, counted_migrations
) -> None:
    from repowise.cli.self_heal import run_editor_migrations, stamp_path

    assert run_editor_migrations() is True
    assert counted_migrations == {"claude": 1, "codex": 1}
    assert stamp_path().read_text(encoding="utf-8") == __version__

    for _ in range(5):
        assert run_editor_migrations() is False
    assert counted_migrations == {"claude": 1, "codex": 1}


def test_an_upgrade_makes_them_run_again(fake_home, counted_migrations) -> None:
    from repowise.cli.self_heal import run_editor_migrations, stamp_path

    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    stamp_path().write_text("0.1.0", encoding="utf-8")

    assert run_editor_migrations() is True
    assert counted_migrations == {"claude": 1, "codex": 1}


def test_skip_editor_setup_touches_no_global_config_at_all(
    fake_home, counted_migrations, monkeypatch
) -> None:
    """The benchmark contract, and the closing half of the migration decision.

    Both migrations write ``~/.claude/settings.json`` and ``~/.codex/hooks.json``
    when they find something to repair, and both used to ignore this variable.
    """
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    from repowise.cli.self_heal import run_editor_migrations

    assert run_editor_migrations() is False
    assert counted_migrations == {"claude": 0, "codex": 0}
    # Not even the stamp: `~/.repowise` is global config too.
    assert list(fake_home.iterdir()) == []


def test_the_stamp_is_not_written_when_a_migration_raised(fake_home, monkeypatch) -> None:
    """A failed run has healed nothing.

    Recording it as done would turn a transient failure into a permanent one, so
    the cost of a broken settings file is the pre-stamp behaviour and no more.
    """
    from repowise.cli.editor_integrations import claude_config, codex_config
    from repowise.cli.self_heal import run_editor_migrations, stamp_path

    def boom() -> bool:
        raise OSError("settings.json is a directory")

    monkeypatch.setattr(claude_config, "migrate_claude_code_hooks", boom)
    monkeypatch.setattr(codex_config, "migrate_codex_rewrite_hook", lambda: False)

    assert run_editor_migrations() is True
    assert not stamp_path().exists()


def test_an_unwritable_stamp_costs_the_old_behaviour_and_nothing_else(
    fake_home, counted_migrations, monkeypatch
) -> None:
    from repowise.cli import self_heal

    monkeypatch.setattr(self_heal, "_write_stamp", lambda version: None)

    assert self_heal.run_editor_migrations() is True
    assert self_heal.run_editor_migrations() is True
    assert counted_migrations == {"claude": 2, "codex": 2}
