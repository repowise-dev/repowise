"""``repowise uninstall``, driven end to end through ``CliRunner``.

Through the command rather than by calling the helpers, deliberately. This track
has already shipped a fix whose test called ``registry.removing()`` by hand and
stayed green when the entire CLI half of the fix was deleted. A removal command
is the last place to accept that.

The home directory is redirected twice over, and both layers matter. The
autouse ``_isolated_home`` fixture in ``tests/unit/cli/conftest.py`` covers
every test in this file including the ones that take only ``tmp_path``; the
``repo`` fixture below re-points ``HOME``/``USERPROFILE``/``Path.home`` at a
second temp dir for the tests that use it. The command can delete
``~/.repowise`` and rewrite ``~/.claude``, and a review of this work already
destroyed a real machine's agent wiring by running the runner without a
redirect, so neither layer is optional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def repo(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("uninstall_home")
    repo_path = tmp_path_factory.mktemp("uninstall_repo")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive) :])
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    (repo_path / ".repowise").mkdir()
    (repo_path / ".repowise" / "wiki.db").write_text("not really a database", encoding="utf-8")
    monkeypatch.chdir(repo_path)
    return repo_path


def _invoke(args: list[str]):
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def _payload(args: list[str]) -> dict:
    result = _invoke([*args, "--format", "json"])
    return json.loads(result.output)


def _wire_everything(repo: Path) -> None:
    """All six targets, not four.

    ``test_every_kept_row_carries_a_reason`` claimed to cover every target while
    hermes and opencode were absent from the scenario entirely, so reverting the
    reason wiring in either left it green.
    """
    CliRunner().invoke(
        cli,
        [
            "agents",
            "add",
            str(repo),
            "--target",
            "claude-code,codex,vscode,cursor,opencode,hermes",
            "-y",
        ],
        catch_exceptions=False,
    )


def _write_generated_blocks(repo: Path) -> tuple[Path, Path]:
    """The two blocks the generators write, with the user's own prose around them."""
    from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

    def fenced(tag: str) -> str:
        start = BaseEditorFileGenerator.MARKER_START_FMT.format(tag=tag)
        end = BaseEditorFileGenerator.MARKER_END_FMT.format(tag=tag)
        return f"{start}\ngenerated body\n{end}"

    claude = repo / ".claude" / "CLAUDE.md"
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(f"# CLAUDE.md\n\nmy own notes\n\n{fenced('REPOWISE')}\n", encoding="utf-8")

    agents = repo / AgentsMdGenerator.filename
    agents.write_text(
        f"# AGENTS.md\n\nmy own notes\n\n{fenced('REPOWISE_AGENTS')}\n", encoding="utf-8"
    )
    return claude, agents


# ---------------------------------------------------------------------------
# The flags, and the one that does not exist
# ---------------------------------------------------------------------------


def test_yes_is_refused_with_the_flag_to_use_instead(repo: Path) -> None:
    """``--yes`` means 'run the default' everywhere else, and there is no safe default here."""
    result = _invoke(["uninstall", str(repo), "--yes"])
    assert result.exit_code != 0
    assert "--all" in result.output
    assert "--keep-index" in result.output
    assert (repo / ".repowise").exists()


def test_all_and_keep_index_together_are_refused(repo: Path) -> None:
    result = _invoke(["uninstall", str(repo), "--all", "--keep-index"])
    assert result.exit_code != 0
    assert (repo / ".repowise").exists()


def test_no_terminal_and_no_scope_flag_removes_nothing_and_exits_non_zero(repo: Path) -> None:
    """Consent is never inferred from a tty probe, in either direction."""
    _wire_everything(repo)
    result = _invoke(["uninstall", str(repo)])

    assert result.exit_code != 0
    assert (repo / ".repowise").exists()
    assert (repo / ".mcp.json").exists()
    # It still says where it looked, which is the half that makes the refusal useful.
    assert str(repo / ".repowise") in result.output
    assert "--all" in result.output


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_changes_nothing_at_all(repo: Path) -> None:
    _wire_everything(repo)
    _write_generated_blocks(repo)
    before = sorted(str(p.relative_to(repo)) for p in repo.rglob("*"))

    result = _invoke(["uninstall", str(repo), "--all", "--dry-run"])

    assert result.exit_code == 0
    assert sorted(str(p.relative_to(repo)) for p in repo.rglob("*")) == before


def test_dry_run_names_every_path_the_real_run_touches(repo: Path) -> None:
    """The claim worth testing is plan-against-results, not plan-against-plan.

    An earlier version of this test compared ``dry["plan"]`` to ``real["plan"]``,
    which are two calls to the same pure function over the same tree. It would
    have stayed green if ``execute`` had ignored the plan completely.
    """
    _wire_everything(repo)
    _write_generated_blocks(repo)

    dry = _payload(["uninstall", str(repo), "--all", "--dry-run"])
    real = _payload(["uninstall", str(repo), "--all"])

    assert dry["dry_run"] is True
    assert real["dry_run"] is False
    assert dry["groups"] == real["groups"]

    planned = {Path(item["path"]) for item in dry["plan"]["items"]}
    touched = [
        Path(result["path"])
        for result in real["results"]
        if result["action"] in ("removed", "failed")
    ]
    # At or under an announced path. The plan names `~/.codex/prompts` once
    # while the report names the eighteen files inside it, and a user shown the
    # directory has been told about its contents. What must never happen is a
    # removal somewhere the plan did not mention at all.
    unannounced = [
        path
        for path in touched
        if path not in planned and not any(parent in planned for parent in path.parents)
    ]
    assert not unannounced, f"removed paths the dry run never named: {sorted(unannounced)}"


def test_dry_run_honours_the_scope_flag_it_was_given(repo: Path) -> None:
    """The pre-flight has to describe the run it is a pre-flight for.

    With the dry-run branch ahead of ``--keep-index``, this reported that the
    index and the machine-wide login would go, which is the opposite of what
    ``--keep-index`` actually does.
    """
    _wire_everything(repo)

    kept = _payload(["uninstall", str(repo), "--keep-index", "--dry-run"])
    everything = _payload(["uninstall", str(repo), "--all", "--dry-run"])

    assert "index" not in kept["groups"]
    assert "global" not in kept["groups"]
    assert "index" in everything["groups"]
    assert kept["groups"] != everything["groups"]


def test_a_repo_that_never_had_repowise_reports_no_agent_wiring(tmp_path: Path) -> None:
    """Existence is not evidence, and most of these files belong to the host.

    Only three of the eighteen paths the six targets describe are ours alone.
    Keying the inventory on existence listed every Claude Code, Codex and Cursor
    user's own config as ours, pre-ticked the agent group in a repo where
    repowise had never run, and had the runner call user-scope uninstalls for
    scopes detection never found.
    """
    from repowise.cli.uninstall import build_plan
    from repowise.cli.uninstall.inventory import Group

    stranger = tmp_path / "stranger"
    (stranger / ".vscode").mkdir(parents=True)
    (stranger / ".vscode" / "extensions.json").write_text(
        '{"recommendations": ["ms-python.python"]}', encoding="utf-8"
    )
    (stranger / "AGENTS.md").write_text("# AGENTS.md\n\nmy own notes\n", encoding="utf-8")
    (stranger / ".mcp.json").write_text(
        '{"mcpServers": {"someone-else": {"command": "x"}}}', encoding="utf-8"
    )

    plan = build_plan(stranger)

    assert plan.for_groups(frozenset({Group.AGENTS})) == []
    assert Group.AGENTS not in plan.groups_present()


def test_a_config_naming_us_is_found_even_when_it_cannot_be_parsed(tmp_path: Path) -> None:
    """The other half of the same rule: the probe is bytes, not a parse.

    The sibling file is what makes this discriminate. Under the old existence
    check both files were listed, so asserting only on the first one passed
    either way; it pinned the previous round's behaviour, not this one's.
    """
    from repowise.cli.uninstall import build_plan
    from repowise.cli.uninstall.inventory import Group

    repo = tmp_path / "jsonc"
    (repo / ".vscode").mkdir(parents=True)
    config = repo / ".vscode" / "mcp.json"
    config.write_text(
        '{\n  // a comment VS Code accepts and our parser does not\n'
        '  "servers": {"repowise": {}}\n}\n',
        encoding="utf-8",
    )
    # Present, ours by path, and naming someone else entirely.
    stranger = repo / ".cursor" / "mcp.json"
    stranger.parent.mkdir(parents=True)
    stranger.write_text('{"mcpServers": {"someone-else": {}}}', encoding="utf-8")

    listed = {item.path for item in build_plan(repo).for_groups(frozenset({Group.AGENTS}))}

    assert config in listed
    assert stranger not in listed


def test_codex_user_scope_is_found_from_the_prompts_directory_alone(tmp_path: Path) -> None:
    """Install writes the prompts unconditionally; detection reads only the hook.

    A directory answering False to the probe left all eighteen prompt files on
    disk under "everything selected is gone".
    """
    from repowise.cli.uninstall import build_plan
    from repowise.cli.uninstall.inventory import Group

    prompts = Path.home() / ".codex" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "repowise-ask.md").write_text("body", encoding="utf-8")
    # JSONC, so `detect` cannot see the hook even if one is there.
    hooks = Path.home() / ".codex" / "hooks.json"
    hooks.write_text("{\n  // a comment\n}\n", encoding="utf-8")

    listed = {item.path for item in build_plan(tmp_path).for_groups(frozenset({Group.AGENTS}))}

    assert prompts in listed


def test_a_bare_dry_run_needs_no_scope_flag(repo: Path) -> None:
    """It asks nobody anything, so it must not route through the prompt."""
    _wire_everything(repo)
    result = _invoke(["uninstall", str(repo), "--dry-run"])

    assert result.exit_code == 0
    assert (repo / ".mcp.json").exists()


def test_json_output_is_parseable_when_no_scope_was_given(repo: Path) -> None:
    """A rich table on the payload's own stream is output no parser can read."""
    result = _invoke(["uninstall", str(repo), "--format", "json"])

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["groups"] == []
    assert "--all" in payload["error"]


# ---------------------------------------------------------------------------
# what it removes
# ---------------------------------------------------------------------------


def test_all_removes_the_wiring_the_index_and_the_generated_blocks(repo: Path) -> None:
    _wire_everything(repo)
    claude, agents = _write_generated_blocks(repo)
    assert (repo / ".mcp.json").exists()

    result = _invoke(["uninstall", str(repo), "--all"])

    assert not (repo / ".repowise").exists()
    assert not (repo / ".mcp.json").exists()
    assert not (repo / ".vscode").exists()
    assert not (repo / ".codex").exists()
    assert "REPOWISE:START" not in claude.read_text(encoding="utf-8")
    assert "REPOWISE_AGENTS:START" not in agents.read_text(encoding="utf-8")
    assert result.exit_code == 0


def test_the_users_own_prose_survives_the_block_removal(repo: Path) -> None:
    """The whole reason these are marker-fenced rather than whole files."""
    claude, agents = _write_generated_blocks(repo)

    _invoke(["uninstall", str(repo), "--all"])

    assert "my own notes" in claude.read_text(encoding="utf-8")
    assert "my own notes" in agents.read_text(encoding="utf-8")


def test_keep_index_leaves_the_index_and_takes_everything_else(repo: Path) -> None:
    _wire_everything(repo)
    _write_generated_blocks(repo)

    result = _invoke(["uninstall", str(repo), "--keep-index"])

    assert (repo / ".repowise" / "wiki.db").exists()
    assert not (repo / ".mcp.json").exists()
    assert result.exit_code == 0


def test_machine_wide_state_is_never_pre_selected() -> None:
    """It goes only when the user names it, because we cannot see the other repos."""
    from repowise.cli.uninstall.inventory import DEFAULT_GROUPS, Group

    assert Group.GLOBAL not in DEFAULT_GROUPS
    assert Group.INDEX not in DEFAULT_GROUPS


def test_global_state_goes_only_when_asked_and_says_what_that_cost(repo: Path) -> None:
    global_dir = Path.home() / ".repowise"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "platform.json").write_text('{"telemetry_enabled": false}', encoding="utf-8")

    kept = _invoke(["uninstall", str(repo), "--keep-index"])
    assert global_dir.exists(), kept.output

    result = _invoke(["uninstall", str(repo), "--all"])
    assert not global_dir.exists()
    assert "telemetry" in result.output


# ---------------------------------------------------------------------------
# honesty
# ---------------------------------------------------------------------------


def test_a_second_run_reports_not_found_and_never_reports_removed(
    repo: Path, monkeypatch
) -> None:
    """Two real shell commands, not two invocations sharing one process.

    The env var the command sets to stop telemetry recreating ``~/.repowise``
    persists in-process, so without clearing it between runs this test suppressed
    the very recreation it was meant to catch, and passed over three separate
    live bugs.
    """
    _wire_everything(repo)
    _write_generated_blocks(repo)
    _invoke(["uninstall", str(repo), "--all"])
    monkeypatch.delenv("REPOWISE_TELEMETRY_DISABLED", raising=False)

    payload = _payload(["uninstall", str(repo), "--all"])

    actions = {result["action"] for result in payload["results"]}
    assert "removed" not in actions, f"not idempotent: {payload['results']}"
    assert actions <= {"not-found", "kept"}


def test_machine_wide_state_stays_gone_across_processes(repo: Path, monkeypatch) -> None:
    """Three separate code paths recreated it at startup or shutdown."""
    global_dir = Path.home() / ".repowise"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "platform.json").write_text("{}", encoding="utf-8")

    _invoke(["uninstall", str(repo), "--all"])
    monkeypatch.delenv("REPOWISE_TELEMETRY_DISABLED", raising=False)
    assert not global_dir.exists()

    _invoke(["uninstall", str(repo), "--all"])
    assert not global_dir.exists(), "our own process put it back"


def test_the_inventory_is_printed_even_when_nothing_is_there(repo: Path) -> None:
    """A trust command that prints nothing on a clean machine has proven nothing."""
    result = _invoke(["uninstall", str(repo), "--all", "--dry-run"])

    assert str(repo / ".repowise") in result.output
    assert "pip uninstall repowise" in result.output or "uninstall repowise" in result.output


def test_a_malformed_marker_pair_is_refused_and_the_row_says_why(repo: Path) -> None:
    """An orphaned marker means the block's end is unknowable. Refuse, do not guess."""
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

    start = BaseEditorFileGenerator.MARKER_START_FMT.format(tag="REPOWISE_AGENTS")
    agents = repo / "AGENTS.md"
    agents.write_text(f"# AGENTS.md\n\n{start}\ngenerated body\n\nmy own notes\n", encoding="utf-8")

    payload = _payload(["uninstall", str(repo), "--all"])

    # By group as well as path: `AGENTS.md` now legitimately appears twice, once
    # for the generated block and once per agent that manages the distill block.
    rows = [
        r for r in payload["results"] if r["path"] == str(agents) and r["group"] == "repo-files"
    ]
    assert rows and rows[0]["action"] == "kept", payload["results"]
    assert rows[0]["reason"]
    assert "my own notes" in agents.read_text(encoding="utf-8")


def test_a_refusal_exits_distinctly_from_a_misinvocation(repo: Path) -> None:
    """Ran fine and something is still here is not the same answer as all gone.

    And not the same answer as "you typed it wrong" either, which is why this is
    3 and not 2: Click already spends 2 on ``UsageError``, so sharing it left a
    script unable to tell a leftover from a bad command line.
    """
    from repowise.cli.uninstall.runner import EXIT_LEFTOVERS
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

    start = BaseEditorFileGenerator.MARKER_START_FMT.format(tag="REPOWISE_AGENTS")
    (repo / "AGENTS.md").write_text(f"{start}\nbody\n", encoding="utf-8")

    result = _invoke(["uninstall", str(repo), "--all"])
    assert result.exit_code == EXIT_LEFTOVERS

    misinvoked = _invoke(["uninstall", str(repo), "--all", "--keep-index"])
    assert misinvoked.exit_code != EXIT_LEFTOVERS

    payload = _payload(["uninstall", str(repo), "--all"])
    assert payload["complete"] is False
    assert payload["leftovers"]


def test_every_kept_row_carries_a_reason(repo: Path) -> None:
    """A bare 'kept' is indistinguishable from a bug.

    Arranged so that several different targets refuse, not just one. The earlier
    version produced exactly one ``kept`` row, from the generated-block path, so
    reverting the reason wiring in any of the five agent targets left it green.
    """
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

    _wire_everything(repo)

    # An orphaned DISTILL marker, so the three AGENTS.md managers each refuse
    # rather than reporting not-found. Overwriting the file wholesale, as an
    # earlier version did, deleted that block and left opencode and hermes
    # emitting zero kept rows, so reverting their reason wiring stayed green
    # while the docstring claimed otherwise.
    generated = BaseEditorFileGenerator.MARKER_START_FMT.format(tag="REPOWISE_AGENTS")
    distill_start = "<!-- REPOWISE_DISTILL:START — Do not edit below this line. Auto-generated by Repowise. -->"
    (repo / "AGENTS.md").write_text(
        f"# AGENTS.md\n\n{generated}\nbody\n\n{distill_start}\nbody\n", encoding="utf-8"
    )
    # JSONC, which these hosts accept and our strict parse refuses.
    (repo / ".vscode" / "mcp.json").write_text(
        '{\n  // a comment\n  "servers": {"repowise": {}}\n}\n', encoding="utf-8"
    )
    (repo / ".cursor" / "mcp.json").write_text(
        '{\n  // a comment\n  "mcpServers": {"repowise": {}}\n}\n', encoding="utf-8"
    )
    (repo / ".codex" / "config.toml").write_text("bad = = toml [[[\n", encoding="utf-8")
    (repo / ".vscode" / "extensions.json").write_text(
        '{\n  // a comment\n  "recommendations": ["repowise-dev.repowise"]\n}\n', encoding="utf-8"
    )
    # Claude Code was the sixth target and the only one this scenario never made
    # refuse, so its reason wiring and both its leftover probes were unpinned.
    (Path.home() / ".claude" / "settings.json").write_text(
        '{\n  // a comment\n  "mcpServers": {"repowise": {}}\n}\n', encoding="utf-8"
    )

    payload = _payload(["uninstall", str(repo), "--all"])

    kept = [r for r in payload["results"] if r["action"] == "kept"]
    labels = {r["label"] for r in kept}
    assert len({r["group"] for r in kept}) > 1, kept
    # Every target that can refuse here does, so a reverted reason in any one of
    # them turns this red rather than only the three that happened to be wired.
    for expected in ("OpenCode", "Hermes", "Codex", "Cursor", "VS Code", "Claude Code"):
        assert any(expected in label for label in labels), (expected, sorted(labels))
    assert all(r["reason"] for r in kept), [r for r in kept if not r["reason"]]


def test_a_config_we_cannot_parse_is_named_rather_than_missed(repo: Path) -> None:
    """Detection cannot read JSONC, and a file we cannot detect is still ours.

    Without the path-existence fallback in the inventory, this file never
    entered the plan at all: the command removed nothing, said nothing about it,
    and printed that everything selected was gone.
    """
    _wire_everything(repo)
    config = repo / ".vscode" / "mcp.json"
    config.write_text(
        '{\n  // repowise, via a comment VS Code accepts\n  "servers": {"repowise": {}}\n}\n',
        encoding="utf-8",
    )

    result = _invoke(["uninstall", str(repo), "--all"])
    payload = _payload(["uninstall", str(repo), "--all"])

    assert str(config) in {r["path"] for r in payload["results"]}
    assert result.exit_code != 0
    assert "Everything selected is gone" not in result.output


def test_a_malformed_settings_file_does_not_abort_the_run(repo: Path) -> None:
    """Sweeping every event means meeting shapes we never wrote.

    Nothing pinned this: reverting the isinstance guards in ``_strip_hooks``
    wholesale left 249 tests green, while a ``"command": 7`` anywhere in the
    user's settings raised ``TypeError`` out of the middle of an uninstall,
    after an earlier removal had already rewritten the file.
    """
    _wire_everything(repo)
    settings = Path.home() / ".claude" / "settings.json"
    doc = json.loads(settings.read_text(encoding="utf-8"))
    doc.setdefault("hooks", {})["Stop"] = [
        "a bare string",
        {"hooks": "not a list"},
        {"hooks": [{"type": "command", "command": 7}, None]},
    ]
    settings.write_text(json.dumps(doc), encoding="utf-8")

    result = _invoke(["uninstall", str(repo), "--all"])

    assert result.exit_code in (0, 3), result.output
    survived = json.loads(settings.read_text(encoding="utf-8"))
    assert "a bare string" in survived["hooks"]["Stop"]


def test_a_live_update_lock_blocks_the_index_and_names_the_reason(repo: Path) -> None:
    from repowise.core.update_lock import try_acquire_update_lock

    # ``None`` means acquired; a payload means someone else holds it.
    assert try_acquire_update_lock(repo, None) is None

    payload = _payload(["uninstall", str(repo), "--all"])

    rows = [r for r in payload["results"] if r["group"] == "index"]
    assert rows and rows[0]["action"] == "kept"
    assert "update" in (rows[0]["reason"] or "")
    assert (repo / ".repowise").exists()


def test_the_package_and_the_plugin_are_named_rather_than_touched(repo: Path) -> None:
    payload = _payload(["uninstall", str(repo), "--all", "--dry-run"])

    advisories = " ".join(payload["plan"]["advisories"])
    assert "uninstall repowise" in advisories
    assert "print-config" in advisories


# ---------------------------------------------------------------------------
# the shared file
# ---------------------------------------------------------------------------


def test_a_batch_removal_clears_the_shared_agents_block(repo: Path) -> None:
    """Three targets manage the distill block; removing them together frees it."""
    CliRunner().invoke(
        cli,
        ["agents", "add", str(repo), "--target", "codex,opencode,hermes", "-y"],
        catch_exceptions=False,
    )
    agents = repo / "AGENTS.md"
    assert "REPOWISE_DISTILL:START" in agents.read_text(encoding="utf-8")

    _invoke(["uninstall", str(repo), "--all"])

    assert not agents.exists() or "REPOWISE_DISTILL:START" not in agents.read_text(encoding="utf-8")


def test_the_two_agents_md_blocks_are_removed_independently(repo: Path) -> None:
    """``AGENTS.md`` holds two different repowise blocks and they are not one concept.

    OpenCode rather than Codex, because Codex's project install does not write
    ``AGENTS.md`` while its uninstall removes it. That asymmetry is pre-existing
    and recorded in the progress log; this test needs an agent that actually
    writes the distill block.
    """
    CliRunner().invoke(
        cli, ["agents", "add", str(repo), "--target", "opencode", "-y"], catch_exceptions=False
    )
    agents = repo / "AGENTS.md"
    body = agents.read_text(encoding="utf-8")
    assert "REPOWISE_DISTILL:START" in body

    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator

    start = BaseEditorFileGenerator.MARKER_START_FMT.format(tag="REPOWISE_AGENTS")
    end = BaseEditorFileGenerator.MARKER_END_FMT.format(tag="REPOWISE_AGENTS")
    agents.write_text(f"{body}\n{start}\nindexer body\n{end}\n", encoding="utf-8")

    # `--keep-index` is agents + repo-files, so both blocks are in scope and
    # both must go. The point is that they are found by different removers.
    _invoke(["uninstall", str(repo), "--keep-index"])
    remaining = agents.read_text(encoding="utf-8") if agents.exists() else ""
    assert "REPOWISE_AGENTS:START" not in remaining
    assert "REPOWISE_DISTILL:START" not in remaining
