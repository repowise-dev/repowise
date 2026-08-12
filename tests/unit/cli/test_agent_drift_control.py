"""Three drift leaks that were silent, and the machinery that makes them speak.

Grouped because they share one shape: something the CLI wrote, or something the
CLI cannot write, goes out of date and nothing says so. Silence is the failure
mode in each case, so every test here asserts on an *observable*: a doctor issue,
a file on disk, a call count. Never on "the function returned".
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


def test_uninstall_removes_what_repowise_ships_and_leaves_the_rest(fake_home) -> None:
    """The prefix is a namespace, not a proof of ownership.

    `repowise-my-team-workflow.md` is the obvious name for a prompt a user writes
    themselves *about* repowise, and it lives in the same global directory. An
    uninstall that globbed `repowise-*.md` deleted it, unrecoverably. The cost of
    the narrow rule is the retired-command file below, which is the right way
    round: a stale file of ours is a wasted kilobyte.
    """
    codex.write_prompts()
    prompts = codex.user_prompts_dir()
    theirs = prompts / "repowise-my-team-workflow.md"
    theirs.write_text("hand written by the user\n", encoding="utf-8")
    stranger = prompts / "someone-elses.md"
    stranger.write_text("not ours\n", encoding="utf-8")
    retired = prompts / "repowise-retired-command.md"
    retired.write_text("shipped by an older release\n", encoding="utf-8")

    codex.TARGET.uninstall(Scope.USER)

    assert not (prompts / "repowise-risk.md").exists(), "a bundled prompt survived"
    assert theirs.read_text(encoding="utf-8") == "hand written by the user\n"
    assert stranger.exists()
    assert retired.exists(), (
        "the narrow rule cannot tell a retired prompt of ours from a hand-written "
        "one of theirs, and must therefore keep both"
    )


def test_a_prompt_that_is_not_valid_utf8_does_not_crash_the_install(fake_home) -> None:
    """`UnicodeDecodeError` is a ValueError, so an OSError handler misses it.

    Nothing wraps `install`: `agents add`, `agents refresh` and `doctor --repair`
    all call it bare, and the last would abort part-way through having already
    written other agents' configs.
    """
    prompts = codex.user_prompts_dir()
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "repowise-risk.md").write_bytes(b"caf\xe9 in latin-1\n")

    actions = {w.path.name: w.action for w in codex.write_prompts()}

    assert actions["repowise-risk.md"] is FileAction.UPDATED
    assert "café" not in (prompts / "repowise-risk.md").read_text(encoding="utf-8")


def test_one_unwritable_prompt_does_not_stop_the_other_seventeen(fake_home) -> None:
    """Raising here just moved the part-way failure rather than removing it.

    `bundled_prompts()` is sorted, so a directory at `repowise-ask.md` aborted
    before any of the rest were written, and it aborted inside `install()`
    *after* the rewrite hook had been written and recorded. The refusal is a row
    in the result instead, which is what the caller renders.
    """
    (codex.user_prompts_dir() / "repowise-ask.md").mkdir(parents=True)

    actions = {w.path.name: w.action for w in codex.write_prompts()}

    assert actions["repowise-ask.md"] is FileAction.KEPT
    assert actions["repowise-risk.md"] is FileAction.CREATED
    assert actions["repowise-why.md"] is FileAction.CREATED
    assert FileAction.KEPT not in set(actions.values()) - {actions["repowise-ask.md"]}


def test_doctor_still_names_a_missing_rewrite_hook_when_prompts_are_stale(fake_home) -> None:
    """Loosening the early return moved this state into the issues path.

    It silently dropped a true, separately-caused fact (the hook is not
    installed) and it made a false one sayable, because the "predates PreToolUse
    rewriting" branch says the hook "is registered" when there is none.
    """
    codex.write_prompts()
    (codex.user_prompts_dir() / "repowise-risk.md").write_text("old\n", encoding="utf-8")

    report = codex.TARGET.doctor()

    assert report.status.value == "stale"
    assert any("slash commands" in issue for issue in report.issues)
    assert any("rewrite hook is not installed" in issue for issue in report.issues)
    assert not any("is registered but its rewrite" in issue for issue in report.issues)


def test_doctor_reports_prompts_that_have_fallen_behind_the_cli(fake_home) -> None:
    """The drift this branch exists to close, on the surface the CLI *can* fix.

    The Claude Code plugin skew is only reportable because repowise cannot
    rewrite a plugin. These it can rewrite in one command, so silence would be
    strictly worse here than there.
    """
    codex.write_prompts()
    assert codex.stale_prompts() == []

    (codex.user_prompts_dir() / "repowise-risk.md").write_text("from v0.1\n", encoding="utf-8")
    (codex.user_prompts_dir() / "repowise-why.md").unlink()

    assert sorted(codex.stale_prompts()) == ["repowise-risk.md", "repowise-why.md"]

    report = codex.TARGET.doctor()
    assert report.status.value == "stale"
    assert any("slash commands" in issue for issue in report.issues)
    assert report.fix_command == "repowise agents add --target=codex"
    # `refresh` only touches what `detect` finds, and prompts are not a
    # registration, so `--repair` would skip this and report success.
    assert report.repairable is False


def test_no_prompts_installed_at_all_is_not_reported_as_stale(fake_home) -> None:
    """An agent nobody wired up is not a stale install."""
    assert codex.stale_prompts() == []
    codex.user_prompts_dir().mkdir(parents=True)
    assert codex.stale_prompts() == []


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


def _equivalent_spellings() -> list[str]:
    """The same release as ``__version__``, written differently.

    Derived rather than hardcoded, so this keeps testing the property after the
    next version bump instead of quietly testing a stale literal.
    """
    parts = __version__.split(".")
    spellings = [__version__, __version__ + ".0", "v" + __version__, "V" + __version__]
    if parts[-1] == "0":
        spellings.append(".".join(parts[:-1]))
        spellings.append("v" + ".".join(parts[:-1]))
    return spellings


@pytest.mark.parametrize("spelling", _equivalent_spellings())
def test_a_version_spelled_differently_is_the_same_release(fake_home, spelling) -> None:
    """String equality made an equivalent version a permanent, unclearable STALE.

    It also sent the user to `pip install -U repowise`, which changes nothing,
    and fired a pointless global refresh on every `doctor --repair`. The module
    already used release-tuple comparison one block later.
    """
    _install_plugin(fake_home, spelling)

    assert claude_code.plugin_version_skew() == []
    assert claude_code.TARGET.doctor().status.value == "ok"


@pytest.mark.parametrize(
    "version,expected",
    [("0.41.0rc1", ["0.41.0rc1"]), ("0.41.0.post1", ["0.41.0.post1"]), ("latest", ["latest"])],
)
def test_a_version_that_is_not_a_plain_release_is_still_reported(
    fake_home, version, expected
) -> None:
    """A release candidate is not the release.

    `core.upgrade.release.parse_release` answers a different question, "is there
    a newer release", so it drops everything after the first non-digit and reads
    all three of these as a plain release. Right for an upgrade prompt, wrong
    here, which is why this module has its own key function.
    """
    _install_plugin(fake_home, version)

    assert claude_code.plugin_version_skew() == expected


def test_the_skew_report_does_not_claim_repair_can_fix_it(fake_home) -> None:
    """`--repair` runs `agents refresh`, which cannot rewrite a host-managed plugin.

    Letting it try bought a global config write that changed nothing, then
    printed advice for a stale matcher and a damaged file, neither of which the
    user has, while omitting the one command that works.
    """
    _install_plugin(fake_home, "0.16.0")

    assert claude_code.TARGET.doctor().repairable is False


def test_a_skewed_plugin_does_not_suppress_the_stale_matcher_repair(
    fake_home, monkeypatch
) -> None:
    """The repair that `repairable` must not take away.

    A stale matcher is rewritten by refresh. Setting `repairable=False` because a
    plugin is also out of date left the matcher installed, parsing, never
    firing, with `doctor --repair` printing "Nothing to repair."
    """
    from repowise.cli.editor_integrations import claude_config

    monkeypatch.setattr(claude_config, "claude_code_rewrite_hook_matcher", lambda: "OldToolName")
    _install_plugin(fake_home, __version__)

    matcher_only = claude_code.TARGET.doctor()
    assert matcher_only.repairable is True

    _install_plugin(fake_home, "0.16.0")
    with_skew = claude_code.TARGET.doctor()

    assert with_skew.repairable is True, "the skew suppressed a repair that works"
    # The printed command names the half `--repair` cannot do.
    assert with_skew.fix_command == claude_code.PLUGIN_UPDATE_COMMAND


def test_a_damaged_settings_file_is_not_offered_to_the_repair_pass(fake_home) -> None:
    """Its own comment says refresh would skip it and report success."""
    settings = fake_home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{ not json", encoding="utf-8")

    report = claude_code.TARGET.doctor()

    assert report.status.value == "broken"
    assert report.repairable is False


def test_an_install_record_with_no_version_is_not_reported_as_skew(fake_home) -> None:
    """Unactionable, so silent, the same degradation `_plugin_installs` makes."""
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
    ``self_heal`` imports them inside the function, and patching a name it has not
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
    assert stamp_path().read_text(encoding="utf-8").startswith(__version__)

    for _ in range(5):
        assert run_editor_migrations() is False
    assert counted_migrations == {"claude": 1, "codex": 1}


def test_an_upgrade_makes_them_run_again(fake_home, counted_migrations) -> None:
    from repowise.cli.self_heal import run_editor_migrations, stamp_path

    stamp_path().parent.mkdir(parents=True, exist_ok=True)
    stamp_path().write_text("0.1.0 claude_code_hooks,codex_rewrite_hook", encoding="utf-8")

    assert run_editor_migrations() is True
    assert counted_migrations == {"claude": 1, "codex": 1}


def test_a_new_migration_runs_even_without_a_version_bump(
    fake_home, counted_migrations, monkeypatch
) -> None:
    """The version alone is not a complete key.

    `__version__` is a literal in `repowise/cli/__init__.py`. A migration added
    in a commit that does not bump it would be permanently skipped for anyone who
    had already run that version, which is every install tracking `main`,
    including this repo's own. The stamp records the migration *names* too.
    """
    from repowise.cli import self_heal

    assert self_heal.run_editor_migrations() is True
    assert self_heal.run_editor_migrations() is False
    before = self_heal.stamp_path().read_text(encoding="utf-8")

    ran: list[str] = []
    monkeypatch.setattr(
        self_heal,
        "_MIGRATIONS",
        (*self_heal._MIGRATIONS, ("a_third_thing", lambda: ran.append("x"))),
    )

    assert self_heal.run_editor_migrations() is True, "a new migration was skipped"
    assert ran == ["x"]
    assert self_heal.stamp_path().read_text(encoding="utf-8") != before


def test_a_stamp_that_is_a_directory_degrades_to_running_them(
    fake_home, counted_migrations
) -> None:
    from repowise.cli.self_heal import run_editor_migrations, stamp_path

    stamp_path().mkdir(parents=True)

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
