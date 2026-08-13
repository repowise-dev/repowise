"""Contract tests for the agent-target seam.

Parameterized across the whole registry rather than written per agent, so a
fourth target inherits the contract by being registered. That is the property
the seam exists to provide, and a per-target test file would quietly not have
it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from repowise.cli.agent_targets import formats  # noqa: F401  (package import smoke)
from repowise.cli.agent_targets.formats import json_merge, marker_block
from repowise.cli.agent_targets.registry import (
    detect_all,
    get_target,
    list_target_ids,
    resolve_target_flag,
    tier_of,
)
from repowise.cli.agent_targets.types import (
    AgentTarget,
    FileAction,
    Scope,
    Tier,
    WriteResult,
    capabilities_of,
    derive_tier,
)

ALL_IDS = list_target_ids()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_the_shipped_targets() -> None:
    """A frozen list, so adding a target is a deliberate edit rather than a side effect.

    Order is load-bearing: it is the order agents appear in prompts, in
    ``--target=all`` and in listings, and new ids append rather than sort in.
    """
    assert ALL_IDS == ["claude-code", "codex", "vscode", "cursor", "opencode", "hermes"]


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_every_registered_target_satisfies_the_protocol(target_id: str) -> None:
    target = get_target(target_id)
    assert isinstance(target, AgentTarget)
    assert target.id == target_id


def test_unknown_id_resolves_to_none_rather_than_raising() -> None:
    assert get_target("nope") is None


def test_resolve_target_flag_handles_the_four_forms(tmp_path: Path) -> None:
    assert resolve_target_flag("none", tmp_path) == []
    assert [t.id for t in resolve_target_flag("all", tmp_path)] == ALL_IDS
    assert [t.id for t in resolve_target_flag("codex,vscode", tmp_path)] == ["codex", "vscode"]
    # Whitespace and case are user input, not a different intent.
    assert [t.id for t in resolve_target_flag(" CODEX , vscode ", tmp_path)] == [
        "codex",
        "vscode",
    ]


def test_unknown_target_flag_names_the_known_ids(tmp_path: Path) -> None:
    """A typo must not silently resolve to nothing and report success."""
    with pytest.raises(ValueError) as excinfo:
        resolve_target_flag("emacs,codex", tmp_path)
    message = str(excinfo.value)
    assert "emacs" in message
    for target_id in ALL_IDS:
        assert target_id in message
    assert "auto" in message


def test_auto_falls_back_to_claude_code_when_nothing_is_detected(tmp_path: Path) -> None:
    """Least surprise: an empty machine still gets the agent almost everyone has."""
    resolved = resolve_target_flag("auto", tmp_path)
    assert [t.id for t in resolved] == ["claude-code"]


def test_detect_all_returns_a_list_per_target(tmp_path: Path) -> None:
    """Detection reports registrations, never a boolean.

    'Configured: yes' cannot express 'configured twice', which is the state the
    method axis exists to surface.
    """
    detected = detect_all(tmp_path)
    assert set(detected) == set(ALL_IDS)
    assert all(isinstance(v, list) for v in detected.values())


def test_detect_all_survives_a_target_whose_probe_raises(tmp_path: Path, monkeypatch) -> None:
    """One broken probe must not take the whole listing down."""
    from repowise.cli.agent_targets.targets import vscode as vscode_target

    def _boom(_repo_path=None):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(vscode_target.TARGET, "detect", _boom)
    detected = detect_all(tmp_path)
    assert detected["vscode"] == []
    assert set(detected) == set(ALL_IDS)


# ---------------------------------------------------------------------------
# Tier derivation
# ---------------------------------------------------------------------------


def test_tiers_are_derived_from_the_adapters_a_target_names() -> None:
    """Full requires both deep surfaces; VS Code has neither, so it cannot claim it."""
    assert tier_of("claude-code") is Tier.FULL
    assert tier_of("codex") is Tier.FULL
    assert tier_of("vscode") is Tier.GOOD
    assert tier_of("cursor") is Tier.GOOD
    assert tier_of("opencode") is Tier.GOOD
    assert tier_of("hermes") is Tier.GOOD


def test_a_target_cannot_reach_full_without_a_session_adapter() -> None:
    """The structural guarantee: dropping the transcript adapter demotes the tier.

    This is the whole reason the tier is derived rather than declared. If it
    were a field, a target could keep saying Full after losing the surface, and
    the README badge generated from it would repeat the claim.
    """

    class _HooksOnly:
        id = "hooks-only"
        display_name = "Hooks Only"
        docs_url = None
        hook_adapter = "claude-code"
        session_adapter = None
        methods = get_target("claude-code").methods

    assert derive_tier(_HooksOnly()) is Tier.GOOD


def test_a_target_that_writes_nothing_is_paste_config() -> None:
    class _Snippet:
        id = "snippet"
        display_name = "Snippet Only"
        docs_url = None
        hook_adapter = None
        session_adapter = None
        methods = ()

    assert derive_tier(_Snippet()) is Tier.PASTE_CONFIG


def test_capabilities_union_across_install_methods() -> None:
    from repowise.cli.agent_targets.types import Capability

    caps = capabilities_of(get_target("claude-code"))
    # Commands come only from the plugin method, instructions only from direct.
    assert Capability.COMMANDS in caps
    assert Capability.INSTRUCTIONS in caps


# ---------------------------------------------------------------------------
# Target contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_print_config_touches_no_filesystem(target_id: str, tmp_path: Path) -> None:
    """The Paste-config tier depends on this being true, so assert it."""
    target = get_target(target_id)
    repo = tmp_path / "repo"
    repo.mkdir()

    snippet = target.print_config(Scope.PROJECT, repo_path=repo)

    assert snippet.strip()
    assert list(repo.iterdir()) == []


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_describe_paths_predicts_what_install_writes(target_id: str, tmp_path: Path) -> None:
    """A path a target claims it would write must be one install actually writes.

    Predicting a superset is allowed (Codex's AGENTS.md is written by a separate
    opt-in surface); predicting something install never touches is not, because
    that is what a dry-run would show the user.
    """
    target = get_target(target_id)
    repo = tmp_path / "repo"
    repo.mkdir()
    if not target.supports_scope(Scope.PROJECT):
        pytest.skip(f"{target_id} has no project scope")

    predicted = {Path(p) for p in target.describe_paths(Scope.PROJECT, repo_path=repo)}
    result = target.install(Scope.PROJECT, repo_path=repo)
    written = {f.path for f in result.files}

    assert written <= predicted, f"{target_id} wrote a path it did not describe"


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_uninstall_is_safe_when_nothing_was_installed(target_id: str, tmp_path: Path) -> None:
    """Every method must be callable against a machine that never ran install."""
    target = get_target(target_id)
    repo = tmp_path / "repo"
    repo.mkdir()
    for scope in (Scope.USER, Scope.PROJECT):
        result = target.uninstall(scope, repo_path=repo)
        assert isinstance(result, WriteResult)
        assert all(
            f.action in (FileAction.NOT_FOUND, FileAction.KEPT, FileAction.REMOVED)
            for f in result.files
        )


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_doctor_offers_exactly_one_fix_command(target_id: str) -> None:
    """One command, never a list — a diagnostic that offers three has not diagnosed."""
    report = get_target(target_id).doctor()
    assert report.target_id == target_id
    if report.status.value != "ok":
        assert report.fix_command
        assert isinstance(report.fix_command, str)


def test_vscode_declines_user_scope() -> None:
    """Scope support is a real answer, not a shrug: VS Code has no user-level file."""
    assert get_target("vscode").supports_scope(Scope.PROJECT)
    assert not get_target("vscode").supports_scope(Scope.USER)


def test_cursor_declines_user_scope() -> None:
    """One global entry can only name one repo, so this target does not write one."""
    assert get_target("cursor").supports_scope(Scope.PROJECT)
    assert not get_target("cursor").supports_scope(Scope.USER)


def test_cursor_writes_its_own_config_key_not_vs_codes() -> None:
    """The two files disagree, and Cursor does not read ``.vscode/mcp.json``.

    ``mcpServers`` against ``servers`` is the whole reason this is a separate
    target rather than a flag on the VS Code one, so it is pinned rather than
    left to the reader of the descriptor.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = Path.cwd()
    written = json.loads(cursor_target.TARGET.print_config(Scope.PROJECT, repo_path=repo))

    assert set(written) == {"mcpServers"}
    assert "repowise" in written["mcpServers"]
    # Cursor documents ``type`` as required for stdio servers. It is the one
    # field that differs from the repo-shared ``.mcp.json``, and the one most
    # likely to be tidied away as redundant.
    assert written["mcpServers"]["repowise"]["type"] == "stdio"


def test_cursors_registration_carries_the_repo_path_positionally(tmp_path: Path) -> None:
    """The load-bearing accident that makes Cursor work without a special case.

    Cursor launches MCP subprocesses with a working directory that is not the
    workspace root and passes no ``rootUri``, so a server resolving its repo from
    ``cwd`` reads the wrong tree and reports "not indexed" on every call. repowise
    is unaffected only because the shared generator emits the absolute repo path
    as a positional argument. Nothing else forces that to stay true, and the
    failure it would cause looks like an indexing bug rather than a config one.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    args = cursor_target.server_entry(repo)["args"]

    assert str(repo.resolve()).replace("\\", "/") in args


def test_cursor_rules_file_round_trips_to_no_file(tmp_path: Path) -> None:
    """Install then uninstall leaves no stub carrying only our own frontmatter.

    A leftover ``.mdc`` holding nothing but ``alwaysApply: true`` still reads as
    repowise-managed to whoever opens it, and Cursor would still load it on every
    conversation.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    rules = cursor_target.rules_path(repo)

    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert rules.exists()
    assert rules.read_text(encoding="utf-8").startswith("---\nalwaysApply: true\n---\n")

    cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert not rules.exists()


def test_cursor_reinstall_reports_unchanged(tmp_path: Path) -> None:
    """Both files, so ``agents refresh`` on a settled repo reports no movement."""
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    first = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert first.changed

    second = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert not second.changed
    assert {f.action for f in second.files} == {FileAction.UNCHANGED}


def test_cursor_keeps_a_sibling_server_and_a_user_env_block(tmp_path: Path) -> None:
    """The merge is per server, and per key inside ours.

    Same guarantee the other JSON targets give. A user who added an ``env`` block
    to the repowise entry keeps it, and another tool's server is not our business.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = cursor_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other-server"},
                    "repowise": {"command": "stale", "env": {"RUST_LOG": "debug"}},
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    servers = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["other"] == {"command": "other-server"}
    assert servers["repowise"]["env"] == {"RUST_LOG": "debug"}
    # ...while a generated key still wins, so a moved repo repoints.
    assert servers["repowise"]["command"] != "stale"


def test_cursor_declines_a_config_it_cannot_parse(tmp_path: Path) -> None:
    """Declining beats rewriting: a file we misparsed loses whatever we could not read."""
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = cursor_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    original = '// a comment\n{ "mcpServers": {} }\n'
    config.write_text(original, encoding="utf-8", newline="\n")

    result = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert config.read_text(encoding="utf-8") == original
    action = next(f.action for f in result.files if f.path == config)
    assert action is FileAction.KEPT
    assert any("mcp.json" in note for note in result.notes)


def test_cursor_refuses_a_rules_file_with_malformed_markers(tmp_path: Path) -> None:
    """The helper refuses; the target has to say so rather than report a write."""
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    rules = cursor_target.rules_path(repo)
    rules.parent.mkdir(parents=True)
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START

    original = f"---\nalwaysApply: true\n---\n\n{DISTILL_MARKER_START}\nI deleted the end.\n"
    rules.write_text(original, encoding="utf-8", newline="\n")

    result = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert rules.read_text(encoding="utf-8") == original
    assert next(f.action for f in result.files if f.path == rules) is FileAction.KEPT
    assert any("unpaired or duplicated" in note for note in result.notes)


def test_cursor_leaves_a_rules_file_it_does_not_own(tmp_path: Path) -> None:
    """Uninstall reports what it did not touch rather than deleting a user's rule.

    ``.cursor/rules/repowise.mdc`` is a name a user could plausibly have written
    themselves, and a marker-free file at that path is theirs. It reports
    ``not-found`` rather than ``kept``: there was no block of ours to remove,
    which is a different statement from "we deliberately left this alone", and
    that second one is reserved for a file we could not safely touch.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    rules = cursor_target.rules_path(repo)
    rules.parent.mkdir(parents=True)
    original = "---\nalwaysApply: true\n---\n\nMy own notes about repowise.\n"
    rules.write_text(original, encoding="utf-8", newline="\n")

    result = cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert rules.read_text(encoding="utf-8") == original
    assert next(f.action for f in result.files if f.path == rules) is FileAction.NOT_FOUND


def test_cursor_uninstall_leaves_no_directories_of_ours_behind(tmp_path: Path) -> None:
    """The directory-shaped version of the stub file ``delete_if_only`` prevents.

    It is not only tidiness. ``is_present`` reads ``.cursor/`` as evidence the
    user has Cursor, so our own residue would keep the agent pre-ticked in every
    later listing for a repo it had just been removed from.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert (repo / ".cursor").is_dir()

    cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert list(repo.iterdir()) == []


def test_cursor_keeps_a_sibling_server_when_it_removes_ours(tmp_path: Path) -> None:
    """Deleting the file is only right when the file held nothing else."""
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    config = cursor_target.mcp_config_path(repo)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["mcpServers"]["other"] = {"command": "other-server"}
    config.write_text(json.dumps(data, indent=2), encoding="utf-8")

    cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"other": {"command": "other-server"}}
    }


def test_cursor_declines_a_hand_wired_remote_server(tmp_path: Path) -> None:
    """The third variant of the preserve-user-keys trap: the key that had to go stayed.

    ``merge_server_entries`` lets generated keys win and keeps everything else,
    which is right for ``command`` and ``args`` and wrong for ``type``. Against
    an entry the user pointed at a remote server it forced ``type`` back to
    ``stdio`` while faithfully preserving the ``url`` beside it, producing an
    entry that was neither a valid local server nor a valid remote one.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = cursor_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    remote = {"mcpServers": {"repowise": {"type": "http", "url": "https://example/mcp"}}}
    config.write_text(json.dumps(remote, indent=2), encoding="utf-8", newline="\n")

    result = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert json.loads(config.read_text(encoding="utf-8")) == remote
    assert next(f.action for f in result.files if f.path == config) is FileAction.KEPT
    assert any("remote server" in note for note in result.notes)


def test_cursor_rules_file_that_is_not_utf8_is_refused_not_replaced(tmp_path: Path) -> None:
    """The dangerous half of the decode bug, and the reason for ``UNREADABLE``.

    A decode failure stops the *read* and not the write. Reporting an
    undecodable file as absent, which is what an unreadable one used to get,
    means ``upsert`` replaces a file it never saw. The crash was the visible
    half; this is the one that loses data.

    A cp1252 rules file is ordinary on Windows, not exotic.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    path = cursor_target.rules_path(repo)
    path.parent.mkdir(parents=True)
    path.write_bytes("caf\xe9".encode("latin-1"))
    before = path.read_bytes()

    result = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert path.read_bytes() == before
    assert next(f.action for f in result.files if f.path == path) is FileAction.KEPT


def test_cursor_detect_survives_a_config_that_is_not_utf8(tmp_path: Path) -> None:
    """``detect`` is contracted never to raise, and ran on paths that do not catch.

    ``UnicodeDecodeError`` is a ``ValueError`` and not a ``json.JSONDecodeError``,
    so naming the latter let it through. ``resolve_target_flag`` calls detection
    without a handler.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = cursor_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    config.write_bytes("caf\xe9".encode("latin-1"))

    assert cursor_target.TARGET.detect(repo) == []
    assert resolve_target_flag("auto", repo) is not None


def test_cursor_leaves_a_cursor_dir_it_did_not_create(tmp_path: Path) -> None:
    """Pruning is gated on having removed something, not on the directory being empty.

    "``rmdir`` refuses a non-empty directory, so an empty one must be ours" is
    the same wrong assumption this module keeps meeting. A user who made
    ``.cursor/`` by hand and left it empty had it deleted by a remove that found
    nothing of ours to remove.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cursor").mkdir()

    result = cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert (repo / ".cursor").is_dir()
    assert not result.changed


def test_cursor_install_repoints_a_stale_local_entry_carrying_a_url(tmp_path: Path) -> None:
    """A local entry with a leftover ``url`` is stale, not remote.

    Reading ``"url" in entry`` ahead of the declared type contradicted what the
    entry says about itself, and wedged the exact state ``agents add`` exists to
    repoint: every command that could have fixed it reached the same decline.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = cursor_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "repowise": {"type": "stdio", "command": "stale", "url": "https://old"}
                }
            }
        ),
        encoding="utf-8",
    )

    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["repowise"]
    assert entry["command"] != "stale"
    assert entry["type"] == "stdio"


def test_cursor_does_not_claim_it_removed_a_file_it_could_not_delete(tmp_path: Path) -> None:
    """A false success on the destructive verb, which a read-only bit was enough to cause.

    Suppressing the ``unlink`` error reported ``removed`` for a file still
    holding our entry. Falling through to the rewrite restores the loud failure
    the previous code had.
    """
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    config = cursor_target.mcp_config_path(repo)

    real_unlink = Path.unlink

    def _refuse(self, *args, **kwargs):
        if self == config:
            raise PermissionError("read-only")
        return real_unlink(self, *args, **kwargs)

    import unittest.mock

    with unittest.mock.patch.object(Path, "unlink", _refuse):
        cursor_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    # Either the entry is gone or the command failed loudly. What it must not do
    # is report "removed" over a file that still names us.
    assert "repowise" not in json.loads(config.read_text(encoding="utf-8")).get("mcpServers", {})


def test_codex_uninstall_separates_absent_from_left_alone(tmp_path: Path) -> None:
    """The same conflation the Cursor rules file drew, one module over.

    ``AGENTS.md`` is a file users write in, so "left alone" is the common answer
    and reporting it as ``not-found`` says there was nothing of ours there.
    """
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets import codex as codex_target

    repo = tmp_path / "repo"
    repo.mkdir()
    agents_md = repo / "AGENTS.md"

    # Nothing of ours in it.
    agents_md.write_text("# My notes\n", encoding="utf-8", newline="\n")
    result = codex_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert next(f.action for f in result.files if f.path == agents_md) is FileAction.NOT_FOUND

    # A marker pair we must not touch.
    agents_md.write_text(f"{DISTILL_MARKER_START}\nno end marker\n", encoding="utf-8", newline="\n")
    result = codex_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert next(f.action for f in result.files if f.path == agents_md) is FileAction.KEPT


def test_codex_instructions_survive_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    """The marker helper refuses an unreadable file, but this path reads before calling it.

    So the helper's refusal never got the chance to run, and the decode escaped
    straight out of ``hook rewrite install`` and ``hook rewrite status``.
    """
    from repowise.cli.editor_integrations.codex_config import (
        agents_md_distill_section_installed,
        install_agents_md_distill_section,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    agents_md = repo / "AGENTS.md"
    agents_md.write_bytes("caf\xe9".encode("latin-1"))
    before = agents_md.read_bytes()

    assert install_agents_md_distill_section(repo) is None
    assert agents_md_distill_section_installed(repo) is False
    assert agents_md.read_bytes() == before


def test_vscode_install_survives_a_vscode_path_that_is_a_file(tmp_path: Path) -> None:
    """The guard widened for Cursor belonged here too, one line above the one that moved."""
    from repowise.cli.agent_targets.targets import vscode as vscode_target

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vscode").write_text("not a directory\n", encoding="utf-8")

    result = vscode_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert (repo / ".vscode").is_file()
    assert all(f.action is FileAction.KEPT for f in result.files)
    assert result.notes


def test_cursor_install_survives_a_cursor_path_that_is_a_file(tmp_path: Path) -> None:
    """``.cursor`` as a plain file made ``mkdir`` raise straight out of ``install``."""
    from repowise.cli.agent_targets.targets import cursor as cursor_target

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cursor").write_text("not a directory\n", encoding="utf-8")

    result = cursor_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert (repo / ".cursor").is_file()
    assert all(f.action is FileAction.KEPT for f in result.files)
    assert result.notes


def test_the_shared_instruction_body_has_one_home() -> None:
    """Two hosts now carry the same managed block, and prose forks a sentence at a time.

    That is exactly how the plugin skill sets drifted, so the second copy is an
    import rather than a paste.
    """
    from repowise.cli.agent_targets import instructions
    from repowise.cli.editor_integrations import codex_config

    assert codex_config._DISTILL_SECTION is instructions.DISTILL_SECTION
    assert codex_config._DISTILL_MARKER_START is instructions.DISTILL_MARKER_START


def test_claude_code_reports_plugin_and_direct_separately(tmp_path: Path, monkeypatch) -> None:
    """The duplicate-registration case the method axis exists for.

    A machine carrying the plugin *and* a direct install has repowise wired
    twice. Collapsing that to a boolean is what let it stay invisible.
    """
    from repowise.cli.agent_targets.targets import claude_code as target_mod

    manifest = tmp_path / "installed_plugins.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    target_mod.PLUGIN_KEY: [
                        {"scope": "user", "version": "0.16.0", "installPath": "/cache/repowise"}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mcpServers": {"repowise": {"command": "repowise"}}}))

    monkeypatch.setattr(target_mod, "plugin_manifest_path", lambda: manifest)
    monkeypatch.setattr(target_mod, "settings_path", lambda: settings)
    monkeypatch.setattr(target_mod, "desktop_config_path", lambda: None)

    registrations = target_mod.detect()
    methods = [r.method for r in registrations]

    assert "plugin" in methods and "direct" in methods
    plugin = next(r for r in registrations if r.method == "plugin")
    assert plugin.version == "0.16.0"


def test_plugin_detection_degrades_to_absent_on_a_broken_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """An unreadable manifest is not evidence of a registration."""
    from repowise.cli.agent_targets.targets import claude_code as target_mod

    manifest = tmp_path / "installed_plugins.json"
    manifest.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(target_mod, "plugin_manifest_path", lambda: manifest)

    assert target_mod._plugin_installs() == []


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------


def _opencode():
    from repowise.cli.agent_targets.targets import opencode as opencode_target

    return opencode_target


def test_opencode_resolves_its_user_dir_through_xdg_on_every_platform(
    tmp_path: Path, monkeypatch
) -> None:
    """XDG on Windows too, and a blank value is not a value.

    The absence of a Windows branch is the point: ``%APPDATA%/opencode`` is
    where a platform-conventional implementation would land, and OpenCode has
    never read it, so an entry written there is invisible rather than broken.

    The blank guard is the other half. An exported-but-empty ``XDG_CONFIG_HOME``
    is ordinary in trimmed CI images, and a plain truthiness test on it resolves
    the config directory to ``/opencode`` at the filesystem root.
    """
    opencode = _opencode()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert opencode.user_config_dir() == tmp_path / "xdg" / "opencode"

    for blank in ("", "   ", "\t"):
        monkeypatch.setenv("XDG_CONFIG_HOME", blank)
        assert opencode.user_config_dir() == Path.home() / ".config" / "opencode"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert opencode.user_config_dir() == Path.home() / ".config" / "opencode"

    # Surrounding whitespace is stripped before the join, not merely tested for.
    # Leaving it in makes the path *relative*, so the config would be written
    # into a directory named " " inside whatever repo the user was standing in.
    # The blank cases above all pass without this, so they do not pin it.
    monkeypatch.setenv("XDG_CONFIG_HOME", f"  {tmp_path / 'xdg'}  ")
    resolved = opencode.user_config_dir()
    assert resolved.is_absolute()
    assert resolved == tmp_path / "xdg" / "opencode"

    # Not %APPDATA%, whatever the platform and whatever APPDATA says.
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert (tmp_path / "roaming") not in opencode.user_config_dir().parents


def test_opencode_writes_opencodes_config_shape_not_its_neighbours(tmp_path: Path) -> None:
    """``mcp``, ``type: local``, and one combined ``command`` array.

    Every other JSON host repowise writes for keys on ``mcpServers`` and splits
    the invocation into ``command`` plus ``args``. This one does neither, which
    makes it the shape most likely to be quietly made consistent with its
    neighbours, so all three halves are pinned.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    written = json.loads(opencode.TARGET.print_config(Scope.PROJECT, repo_path=repo))

    assert set(written) == {"mcp"}
    entry = written["mcp"]["repowise"]
    assert entry["type"] == "local"
    assert isinstance(entry["command"], list)
    assert "args" not in entry
    # Binary first, then its arguments, in one array.
    assert entry["command"][0] == "repowise"
    assert entry["command"][1] == "mcp"
    assert str(repo.resolve()).replace("\\", "/") in entry["command"]


def test_opencode_user_scope_names_no_repo(tmp_path: Path) -> None:
    """One global entry serves every workspace, so it cannot name one repo.

    The user entry pins the absolute binary of the install that wrote it, so a
    PATH shadow cannot hijack it, and passes no repo path so the server resolves
    whichever repo it was launched in. The project entry does the opposite on
    both counts, and swapping either would be silent.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    user = opencode.server_entry(Scope.USER)
    project = opencode.server_entry(Scope.PROJECT, repo)

    assert str(repo.resolve()).replace("\\", "/") not in user["command"]
    assert str(repo.resolve()).replace("\\", "/") in project["command"]

    # Repo-shared file keeps the bare command, because one contributor's
    # absolute path breaks everyone else's checkout. The per-user one pins the
    # install that wrote it so a PATH shadow cannot hijack the server. Both
    # halves are asserted: checking only the project one leaves the user limb
    # free to be "simplified" to the bare name with nothing going red.
    from repowise.cli.mcp_config import resolve_repowise_command

    assert project["command"][0] == "repowise"
    assert user["command"][0] == resolve_repowise_command()


def test_opencode_round_trips_to_nothing(tmp_path: Path) -> None:
    """Install then uninstall leaves no file and no wrapper key behind.

    A file repowise created holds only ``mcp``, so removing the entry empties
    it and it goes. Nothing is left that still reads as repowise having been
    here.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    config = opencode.config_path(Scope.PROJECT, repo)
    assert config.name == "opencode.jsonc"
    assert "repowise" in json.loads(config.read_text(encoding="utf-8"))["mcp"]

    opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert not config.exists()
    assert not (repo / "opencode.json").exists()
    assert not (repo / "AGENTS.md").exists()


def test_opencode_reinstall_reports_unchanged(tmp_path: Path) -> None:
    """``agents refresh`` on a settled repo reports no movement on either file."""
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    first = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert first.changed

    second = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert not second.changed
    assert {f.action for f in second.files} == {FileAction.UNCHANGED}


def test_opencode_prefers_an_existing_json_over_creating_a_jsonc(tmp_path: Path) -> None:
    """Writing the file the host does not read is the failure this ordering avoids.

    ``.jsonc`` wins when it exists, ``.json`` when only it does, and a brand-new
    file is ``.jsonc`` to match what OpenCode itself creates. What must never
    happen is a second config file appearing beside the one already there.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")

    assert opencode.config_path(Scope.PROJECT, repo).name == "opencode.json"

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert not (repo / "opencode.jsonc").exists()
    assert "repowise" in json.loads((repo / "opencode.json").read_text(encoding="utf-8"))["mcp"]

    (repo / "opencode.jsonc").write_text("{}\n", encoding="utf-8")
    assert opencode.config_path(Scope.PROJECT, repo).name == "opencode.jsonc"


def test_opencode_declines_a_commented_config_rather_than_stripping_it(tmp_path: Path) -> None:
    """A JSONC file is legal here, and repowise ships no parser that preserves it.

    Declining leaves a working config untouched and prints what to add.
    Re-serialising it through a strict parser with comments stripped would
    delete every one of them, which is unrecoverable and silent.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "opencode.jsonc"
    commented = '{\n  // my provider settings\n  "mcp": {}\n}\n'
    config.write_text(commented, encoding="utf-8")

    result = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert config.read_text(encoding="utf-8") == commented
    assert any(f.action is FileAction.KEPT for f in result.files)
    assert any("print-config opencode" in note for note in result.notes)


def test_opencode_keeps_a_sibling_server_and_a_user_environment_block(tmp_path: Path) -> None:
    """The merge is per server, and per key inside ours."""
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "opencode.jsonc"
    config.write_text(
        json.dumps(
            {
                "mcp": {
                    "other": {"type": "local", "command": ["other-server"]},
                    "repowise": {
                        "type": "local",
                        "command": ["stale"],
                        "environment": {"RUST_LOG": "debug"},
                    },
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    servers = json.loads(config.read_text(encoding="utf-8"))["mcp"]
    assert servers["other"] == {"type": "local", "command": ["other-server"]}
    assert servers["repowise"]["environment"] == {"RUST_LOG": "debug"}
    assert servers["repowise"]["command"] != ["stale"]


def test_opencode_does_not_re_enable_a_server_the_user_disabled(tmp_path: Path) -> None:
    """``enabled`` is a switch the user flips, not a field repowise computes.

    ``false`` is the documented way to park a server without deleting it, and
    ``agents refresh`` and ``doctor --repair`` both call ``install``. Forcing it
    back to ``true`` there would silently undo a deliberate choice, which is the
    same class of bug as overwriting a user's ``env`` block.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "opencode.jsonc"
    config.write_text(
        json.dumps({"mcp": {"repowise": {"type": "local", "command": ["x"], "enabled": False}}})
        + "\n",
        encoding="utf-8",
    )

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    entry = json.loads(config.read_text(encoding="utf-8"))["mcp"]["repowise"]
    assert entry["enabled"] is False
    # The rest of the entry is still repointed.
    assert entry["command"] != ["x"]

    # A fresh entry gets it seeded on, so a first install is usable.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    opencode.TARGET.install(Scope.PROJECT, repo_path=fresh)
    written = json.loads((fresh / "opencode.jsonc").read_text(encoding="utf-8"))
    assert written["mcp"]["repowise"]["enabled"] is True


def test_opencode_keeps_a_config_holding_anything_of_the_users(tmp_path: Path) -> None:
    """The file is deleted only when nothing of the user's is in it."""
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "opencode.jsonc"

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["theme"] = "tokyonight"
    config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert config.exists()
    remaining = json.loads(config.read_text(encoding="utf-8"))
    assert remaining["theme"] == "tokyonight"
    assert "mcp" not in remaining


def test_is_damaged_calls_a_non_utf8_config_damaged_rather_than_raising(tmp_path: Path) -> None:
    """It is present, it was opened, and it is not readable JSON. That is the question.

    ``UnicodeDecodeError`` is a ``ValueError``, so neither of this helper's
    handlers caught it and it escaped. Both callers run it inside ``doctor()``,
    so a cp1252 ``settings.json`` or ``hooks.json`` -- ordinary on Windows --
    tracebacked out of ``repowise doctor`` instead of being reported by it.
    Fixed in the shared helper rather than at the two call sites.
    """
    config = tmp_path / "settings.json"
    config.write_bytes(b'{"mcpServers": {}} \xff\xfe caf\xe9')

    assert json_merge.is_damaged(config) is True


def test_every_target_doctor_survives_a_non_utf8_config(tmp_path: Path, monkeypatch) -> None:
    """``doctor`` runs every registered target, so one raising takes the command down.

    The user-level config each target reads is seeded with undecodable bytes
    first. Calling ``doctor()`` against an empty home would pass without the
    fix, which is the shape of test that let this survive in the first place.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    garbage = b'{"a": 1} \xff\xfe caf\xe9'

    home = Path.home()
    for relative in (
        Path(".claude") / "settings.json",
        Path(".codex") / "hooks.json",
        Path(".config") / "opencode" / "opencode.jsonc",
    ):
        target_file = home / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(garbage)
    xdg_config = tmp_path / "xdg" / "opencode" / "opencode.jsonc"
    xdg_config.parent.mkdir(parents=True, exist_ok=True)
    xdg_config.write_bytes(garbage)

    for target_id in ALL_IDS:
        target = get_target(target_id)
        assert target is not None
        target.doctor()


def test_opencode_never_deletes_the_config_file_opencode_itself_created(
    tmp_path: Path, monkeypatch
) -> None:
    """A bare ``{"$schema": ...}`` file is the host's first-run config, not our stub.

    Uninstall used to delete a file holding only that, on the invariant that
    repowise had seeded it. False in the most ordinary case there is: OpenCode
    writes exactly that file on first run. Not seeding a ``$schema`` on create
    is what makes "is this file empty" an unambiguous test.

    The config directory is not pruned either. It is the host's, and "we
    emptied it, so it was ours" is the same false claim one level up.
    """
    opencode = _opencode()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_dir = tmp_path / "xdg" / "opencode"
    config_dir.mkdir(parents=True)
    host_file = config_dir / "opencode.jsonc"
    host_file.write_text('{\n  "$schema": "https://opencode.ai/config.json"\n}\n', encoding="utf-8")

    opencode.TARGET.install(Scope.USER)
    opencode.TARGET.uninstall(Scope.USER)

    assert host_file.exists(), "deleted the host's own config file"
    assert config_dir.is_dir(), "deleted the host's own config directory"
    assert "mcp" not in json.loads(host_file.read_text(encoding="utf-8"))


def test_opencode_creates_no_schema_key_of_its_own(tmp_path: Path) -> None:
    """A file repowise creates holds only ``mcp``, so it empties to nothing."""
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    written = json.loads((repo / "opencode.jsonc").read_text(encoding="utf-8"))

    assert set(written) == {"mcp"}


def test_opencode_removes_an_entry_from_whichever_file_holds_it(tmp_path: Path) -> None:
    """Uninstall and detection sweep both spellings, not the one preferred today.

    Install into a repo holding only ``opencode.json`` writes there. When an
    ``opencode.jsonc`` later appears beside it -- the host creates one, or the
    user does -- ``config_path`` flips to the new file. Uninstall then reported
    success against the empty one while the real registration sat in the old,
    invisible to ``detect`` and so unreachable by every command that could have
    removed it.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert "repowise" in json.loads((repo / "opencode.json").read_text(encoding="utf-8"))["mcp"]

    (repo / "opencode.jsonc").write_text("{}\n", encoding="utf-8")
    assert opencode.config_path(Scope.PROJECT, repo).name == "opencode.jsonc"

    # Still found, so still removable.
    assert [r.config_path.name for r in opencode.TARGET.detect(repo)] == ["opencode.json"]

    opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert opencode.TARGET.detect(repo) == []
    assert not (repo / "opencode.json").exists()


def test_opencode_refresh_does_not_create_a_second_registration(tmp_path: Path) -> None:
    """Writes follow the file already holding the entry, or detection and install disagree.

    Teaching ``detect`` to sweep both spellings without teaching writes the same
    thing was worse than either rule alone: an install that had landed in
    ``opencode.json`` stayed detected there once an ``opencode.jsonc`` appeared
    beside it, so ``agents refresh`` no longer skipped the scope as unwired and
    wrote a **second** entry into the other file. ``doctor --repair`` routes
    through the same refresh. Refresh is contracted to add nothing.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")
    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    (repo / "opencode.jsonc").write_text("{}\n", encoding="utf-8")

    second = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert [r.config_path.name for r in opencode.TARGET.detect(repo)] == ["opencode.json"]
    assert "mcp" not in json.loads((repo / "opencode.jsonc").read_text(encoding="utf-8"))
    assert {f.action for f in second.files} == {FileAction.UNCHANGED}


def test_opencode_doctor_agrees_with_detection_about_which_file_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """``doctor`` reads both spellings, like ``detect`` and ``uninstall``.

    Reading only the preferred one made it contradict ``repowise agents``: wired
    in the listing, not-installed here, and the fix command printed here then
    wrote a second entry into the other file.
    """
    from repowise.cli.agent_targets.types import DoctorStatus

    opencode = _opencode()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_dir = tmp_path / "xdg" / "opencode"
    config_dir.mkdir(parents=True)

    opencode.TARGET.install(Scope.USER)
    # The host's first-run file turns up beside ours and takes preference.
    (config_dir / "opencode.jsonc").rename(config_dir / "opencode.json")
    (config_dir / "opencode.jsonc").write_text(
        '{"$schema": "https://opencode.ai/config.json"}\n', encoding="utf-8"
    )

    assert opencode.TARGET.detect() != []
    assert opencode.TARGET.doctor().status is DoctorStatus.OK


def test_opencode_uninstall_leaves_no_directory_of_ours_behind(
    tmp_path: Path, monkeypatch
) -> None:
    """An empty leftover directory keeps the agent pre-ticked forever.

    ``is_present`` reads the config directory as evidence the user has OpenCode
    and ``install`` creates it, so leaving it made our own residue the reason
    the agent kept being offered on a machine that never had it.
    """
    import shutil

    opencode = _opencode()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert opencode.TARGET.is_present() is False

    opencode.TARGET.install(Scope.USER)
    opencode.TARGET.uninstall(Scope.USER)

    assert opencode.TARGET.is_present() is False


def test_opencode_detects_a_commented_config_that_names_repowise(tmp_path: Path) -> None:
    """A JSONC config is the shape this target blesses, so it must not read as unwired.

    ``other_managers_of`` asks ``detect`` who is still using a shared file, so
    answering "no" for a commented config meant removing Codex stripped the
    managed AGENTS.md block out from under a fully working OpenCode, silently.
    """
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets import codex as codex_target

    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    # OpenCode writes the managed block; Codex's own install does not touch
    # AGENTS.md, which the generation path owns.
    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    # Now the user adds a comment to the config OpenCode reads. Nothing about
    # their setup has changed except that repowise can no longer parse it.
    (repo / "opencode.jsonc").write_text(
        '{\n  // my own note\n  "mcp": {\n    "repowise": {"type": "local", '
        '"command": ["repowise", "mcp"]}\n  }\n}\n',
        encoding="utf-8",
    )

    assert opencode.TARGET.detect(repo) != []

    result = codex_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert DISTILL_MARKER_START in (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert any("OpenCode" in note for note in result.notes)


def test_vscode_declines_a_hand_wired_remote_server(tmp_path: Path) -> None:
    """The same guard, in the sibling that also had the bug.

    VS Code documents ``"type": "http"`` in ``.vscode/mcp.json``, so a remote
    repowise entry is a shape that turns up. The merge would force ``type`` back
    to ``stdio`` while preserving the ``url``, leaving an entry that is neither.
    Ported when the guard was written for OpenCode rather than left for the next
    review to find here.
    """
    from repowise.cli.agent_targets.targets import vscode as vscode_target

    repo = tmp_path / "repo"
    repo.mkdir()
    config = vscode_target.mcp_config_path(repo)
    config.parent.mkdir(parents=True)
    remote = {"servers": {"repowise": {"type": "http", "url": "https://mcp.repowise.dev/mcp"}}}
    config.write_text(json.dumps(remote, indent=2) + "\n", encoding="utf-8")

    result = vscode_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert json.loads(config.read_text(encoding="utf-8"))["servers"]["repowise"] == (
        remote["servers"]["repowise"]
    )
    assert any("remote server" in note for note in result.notes)


def test_opencode_declines_a_hand_wired_remote_server(tmp_path: Path) -> None:
    """Half-converting a remote entry leaves one that is neither.

    The merge lets generated keys win and keeps the rest, which forces ``type``
    back to ``local`` while faithfully preserving the ``url`` beside it. The
    preservation rule is what makes the result broken rather than merely
    overwritten, and this config is ``$schema``-validated, so a stray key can
    cost the whole file rather than the one entry.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "opencode.jsonc"
    remote = {
        "mcp": {"repowise": {"type": "remote", "url": "https://mcp.repowise.dev/sse", "enabled": True}}
    }
    config.write_text(json.dumps(remote, indent=2) + "\n", encoding="utf-8")

    result = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert json.loads(config.read_text(encoding="utf-8")) == remote
    assert any(f.action is FileAction.KEPT for f in result.files)
    assert any("remote server" in note for note in result.notes)


def test_opencode_doctor_does_not_call_a_legal_jsonc_config_broken(
    tmp_path: Path, monkeypatch
) -> None:
    """``BROKEN`` fails the whole doctor run, and a commented config is not damage.

    OpenCode accepts JSONC by design, so a file ``json.loads`` rejects is far
    more likely to be a legal config with a comment in it. Reporting that as
    broken failed ``repowise doctor`` -- and any CI running it -- for someone
    who had installed OpenCode and never touched repowise, then handed them a
    fix command that declines for the same reason.
    """
    from repowise.cli.agent_targets.types import DoctorStatus

    opencode = _opencode()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_dir = tmp_path / "xdg" / "opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.jsonc").write_text(
        '{\n  // my theme\n  "theme": "tokyonight"\n}\n', encoding="utf-8"
    )

    report = opencode.TARGET.doctor()

    assert report.status is not DoctorStatus.BROKEN
    assert any("Comments are legal" in issue for issue in report.issues)


def test_opencode_uninstall_note_names_the_real_reason_it_kept_the_file(tmp_path: Path) -> None:
    """A malformed marker pair is not shared ownership, and the advice differs.

    ``KEPT`` covers three unrelated causes. Blaming the shared block for an
    orphaned marker sends the user to remove an agent, after which the block
    still will not go and nothing has said why.
    """
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets import codex as codex_target

    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    # Break the pair by hand, leaving the start marker orphaned.
    agents_md = repo / "AGENTS.md"
    text = agents_md.read_text(encoding="utf-8")
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_END

    agents_md.write_text(text.replace(DISTILL_MARKER_END, ""), encoding="utf-8")

    result = opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert DISTILL_MARKER_START in agents_md.read_text(encoding="utf-8")
    assert not any("still reads the same managed block" in note for note in result.notes)


def test_opencode_detect_survives_a_config_that_is_not_utf8(tmp_path: Path) -> None:
    """``detect`` is contracted never to raise, and cp1252 files are ordinary.

    ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, so a handler
    naming only ``OSError`` and ``JSONDecodeError`` lets it escape a probe that
    runs on paths which do not catch.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.jsonc").write_bytes(b'{"mcp": {"repowise": {}}} \xff\xfe caf\xe9')

    assert opencode.TARGET.detect(repo) == []


def test_opencode_install_survives_a_config_path_that_is_a_directory(tmp_path: Path) -> None:
    """Nothing wraps ``install``, so an escape aborts a multi-agent run.

    ``agents add``, ``agents refresh`` and ``doctor --repair`` all call it bare,
    and a traceback here replaces the summary naming the agents already written.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.jsonc").mkdir()

    result = opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert any(f.action is FileAction.KEPT for f in result.files)
    assert result.notes


def test_opencode_is_present_checks_each_limb_on_its_own(tmp_path: Path, monkeypatch) -> None:
    """Neither limb may be carried by the other, or the test proves nothing.

    A single assertion over the real machine passes for whichever reason
    happens to hold there, which is how a probe with a dead limb ships green.
    """
    opencode = _opencode()
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert opencode.TARGET.is_present() is False

    # Limb one: the config directory, with nothing on PATH.
    (tmp_path / "xdg" / "opencode").mkdir(parents=True)
    assert opencode.TARGET.is_present() is True

    # Limb two: the binary, with no config directory.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert opencode.TARGET.is_present() is False
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/opencode" if name == "opencode" else None)
    assert opencode.TARGET.is_present() is True


def test_opencode_does_not_read_its_own_output_as_evidence_of_the_agent(tmp_path: Path, monkeypatch) -> None:
    """A repo-local limb would make our own install the reason we keep offering.

    OpenCode keeps nothing repo-local of its own: the project config is a bare
    ``opencode.json`` at the root, which this target may well have written
    itself. Cursor and VS Code can read a repo-local directory as evidence
    because those directories are the editor's, not ours.
    """
    opencode = _opencode()
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert opencode.TARGET.is_present(repo) is False


# ---------------------------------------------------------------------------
# AGENTS.md is shared between targets
# ---------------------------------------------------------------------------


def test_agents_md_survives_removing_one_of_two_agents_that_read_it(tmp_path: Path) -> None:
    """The shared-file case, in both directions.

    ``AGENTS.md`` is a host-neutral convention rather than one agent's private
    config: Codex and OpenCode both manage the same path in the same repo, and
    both are right to. Install is safe because the block is marker-delimited and
    idempotent. Uninstall is not: removing either agent stripped the block out
    from under the other, which stayed wired and silently lost its instructions.

    Asserted for Codex as well as OpenCode on purpose. A guard added only to the
    agent that arrived most recently leaves the identical bug in its sibling.
    """
    from repowise.cli.agent_targets.instructions import (
        DISTILL_MARKER_START,
    )
    from repowise.cli.agent_targets.targets import codex as codex_target

    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    agents_md = repo / "AGENTS.md"

    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert DISTILL_MARKER_START in agents_md.read_text(encoding="utf-8")

    # Removing OpenCode leaves Codex's block alone, and says why.
    result = opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert DISTILL_MARKER_START in agents_md.read_text(encoding="utf-8")
    assert any(f.path == agents_md and f.action is FileAction.KEPT for f in result.files)
    assert any("Codex" in note for note in result.notes)

    # And the mirror: with only Codex left, removing Codex does strip it. The
    # file held nothing but our block, so it goes with it.
    codex_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)
    assert not agents_md.exists()


def test_agents_md_is_kept_by_codex_while_opencode_still_reads_it(tmp_path: Path) -> None:
    """The same guard, driven from the other side."""
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets import codex as codex_target

    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()
    agents_md = repo / "AGENTS.md"

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    result = codex_target.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert DISTILL_MARKER_START in agents_md.read_text(encoding="utf-8")
    assert any(f.path == agents_md and f.action is FileAction.KEPT for f in result.files)
    assert any("OpenCode" in note for note in result.notes)


def test_removing_both_agents_at_once_does_not_deadlock_on_the_shared_file(
    tmp_path: Path,
) -> None:
    """``agents remove --target=all`` must not keep the block for an agent it is removing.

    Each target's uninstall asks who else is still wired, and during a batch
    removal the answer is "the one that has not been processed yet" -- or worse,
    one whose detection is latched on by a file its own uninstall does not
    delete. Both then keep the block on the other's behalf, and each tells the
    user to remove an agent they removed in the same command.

    Asserted in **both orders**, because the failure is order-dependent and the
    order that works proves nothing about the one that does not.
    """
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START
    from repowise.cli.agent_targets.registry import removing
    from repowise.cli.agent_targets.targets import codex as codex_target

    opencode = _opencode()
    for order in ([codex_target.TARGET, opencode.TARGET], [opencode.TARGET, codex_target.TARGET]):
        repo = tmp_path / f"repo-{order[0].id}"
        repo.mkdir()
        agents_md = repo / "AGENTS.md"

        codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)
        opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
        assert DISTILL_MARKER_START in agents_md.read_text(encoding="utf-8")

        with removing(t.id for t in order):
            for target in order:
                target.uninstall(Scope.PROJECT, repo_path=repo)

        assert not agents_md.exists(), f"block survived removal in order {[t.id for t in order]}"


def test_a_target_that_is_not_wired_does_not_hold_the_shared_file(tmp_path: Path) -> None:
    """Only *wired* agents count, or nothing could ever be removed.

    Every descriptor claims paths it has never written, so ``describe_paths``
    alone would make ``AGENTS.md`` permanently unremovable by anyone.
    """
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_START

    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    assert DISTILL_MARKER_START in (repo / "AGENTS.md").read_text(encoding="utf-8")

    # Codex knows the path but has never been wired here.
    opencode.TARGET.uninstall(Scope.PROJECT, repo_path=repo)

    assert not (repo / "AGENTS.md").exists()


# ---------------------------------------------------------------------------
# WriteResult
# ---------------------------------------------------------------------------


def test_write_result_reports_change_only_for_real_movement() -> None:
    result = WriteResult()
    result.record(Path("a.json"), FileAction.UNCHANGED)
    result.record(Path("b.json"), FileAction.NOT_FOUND)
    assert not result.changed

    result.record(Path("c.json"), FileAction.CREATED)
    assert result.changed


def test_write_result_json_projection_is_built_at_the_construction_site() -> None:
    """The projection carries the enum's value, ready for ``--format json``."""
    result = WriteResult()
    result.record(Path("a.json"), FileAction.CREATED)
    result.note("Restart the editor.")

    payload = result.as_dict()
    assert payload["files"][0]["action"] == "created"
    assert payload["notes"] == ["Restart the editor."]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def test_json_deep_equal_ignores_key_order_but_not_content() -> None:
    assert json_merge.json_deep_equal({"a": 1, "b": [1, {"c": 2}]}, {"b": [1, {"c": 2}], "a": 1})
    assert not json_merge.json_deep_equal({"a": 1}, {"a": 1, "b": 2})
    assert not json_merge.json_deep_equal([1, 2], [2, 1])


def test_json_deep_equal_does_not_confuse_bool_and_int() -> None:
    """``True == 1`` in Python; for an idempotency check it must not."""
    assert not json_merge.json_deep_equal({"hooks": True}, {"hooks": 1})


def test_json_deep_equal_separates_int_from_float() -> None:
    """The contract is "the bytes would not move", and 1 and 1.0 serialize differently.

    Treating them as equal would report ``unchanged`` for a write that does
    change the file.
    """
    assert not json_merge.json_deep_equal({"timeout": 1}, {"timeout": 1.0})
    assert json.dumps(1) != json.dumps(1.0)


def test_write_json_config_reports_created_then_unchanged(tmp_path: Path) -> None:
    """The deep-equal-before-write pass, from the caller's side."""
    path = tmp_path / "mcp.json"

    assert json_merge.write_json_config(path, {"a": 1}) is FileAction.CREATED
    assert json_merge.write_json_config(path, {"a": 1}) is FileAction.UNCHANGED
    assert json_merge.write_json_config(path, {"a": 2}) is FileAction.UPDATED


def test_write_json_config_compares_the_bytes_it_would_land(tmp_path: Path) -> None:
    """Not the raw rendering, and not a normalised read — the translated bytes.

    This writer takes the platform newline translation. Comparing the
    untranslated text calls every Windows re-run an update; normalising the
    file's endings instead leaves a CRLF file un-normalised on POSIX. Only the
    bytes that would actually land answer both.
    """
    path = tmp_path / "mcp.json"
    json_merge.write_json_config(path, {"a": 1})
    settled = path.read_bytes()

    assert json_merge.write_json_config(path, {"a": 1}) is FileAction.UNCHANGED
    assert path.read_bytes() == settled

    # A file whose endings do not match this platform's writer is rewritten.
    path.write_bytes(settled.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n" if os.linesep == "\n" else b"\n"))
    assert json_merge.write_json_config(path, {"a": 1}) is FileAction.UPDATED
    assert path.read_bytes() == settled


def test_codex_reinstall_reports_unchanged_and_leaves_config_alone(tmp_path: Path) -> None:
    """The ``.codex/config.toml`` non-idempotency, closed.

    Two table upserts per install, each of which strips its table and re-appends
    it, so run 2 used to swap them back and keep the blank line the swap left.
    The document-level comparison means run 2 writes nothing at all.
    """
    from repowise.cli.agent_targets.targets import codex as codex_target

    repo = tmp_path / "repo"
    repo.mkdir()

    first = codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)
    settled = (repo / ".codex" / "config.toml").read_bytes()
    assert first.changed

    second = codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert not second.changed
    assert {f.action for f in second.files} == {FileAction.UNCHANGED}
    assert (repo / ".codex" / "config.toml").read_bytes() == settled


def test_codex_keeps_user_keys_added_to_its_server_table(tmp_path: Path) -> None:
    """The TOML twin of the ``env``-block fix (#307) the JSON path already had.

    ``replace_table`` rewrites the whole table, so without a merge every key a
    user added to ``[mcp_servers.repowise]`` was dropped on every single write.
    """
    import tomllib

    from repowise.cli.agent_targets.targets import codex as codex_target

    repo = tmp_path / "repo"
    repo.mkdir()
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    config_path = repo / ".codex" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "startup_timeout_sec = 20", 'startup_timeout_sec = 20\nenv_key = "mine"'
        ),
        encoding="utf-8",
    )

    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    table = tomllib.loads(config_path.read_text(encoding="utf-8"))["mcp_servers"]["repowise"]
    assert table["env_key"] == "mine"
    # ...while a generated key still wins, so a moved repo repoints.
    assert table["cwd"] == str(repo.resolve())


def test_codex_preserves_an_env_table_rather_than_choking_on_it(tmp_path: Path) -> None:
    """``env`` is the key most likely to be there, and it is a table.

    Preserving a user's keys means re-rendering them, so the narrow serializer
    meets a dict the first time anyone has an env block — the standard case,
    not an exotic one. It renders as an inline table.
    """
    import tomllib

    from repowise.cli.agent_targets.targets import codex as codex_target

    repo = tmp_path / "repo"
    repo.mkdir()
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    config_path = repo / ".codex" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "startup_timeout_sec = 20",
            'startup_timeout_sec = 20\nenv = { RUST_LOG = "debug" }\ntimeout = 1.5',
        ),
        encoding="utf-8",
    )

    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    table = tomllib.loads(config_path.read_text(encoding="utf-8"))["mcp_servers"]["repowise"]
    assert table["env"] == {"RUST_LOG": "debug"}
    assert table["timeout"] == 1.5


def test_codex_refuses_a_value_it_cannot_rewrite_without_a_traceback(tmp_path: Path) -> None:
    """This runs inside ``init`` with no try around it, so it must not raise raw.

    Every other refusal in this writer is a ClickException naming the file and
    saying nothing was written; a bare TypeError mid-``init`` is neither.
    """
    import click

    from repowise.cli.agent_targets.targets import codex as codex_target

    repo = tmp_path / "repo"
    repo.mkdir()
    codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    config_path = repo / ".codex" / "config.toml"
    before = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        before.replace("startup_timeout_sec = 20", "startup_timeout_sec = 20\nports = [1, 2]"),
        encoding="utf-8",
    )
    poisoned = config_path.read_text(encoding="utf-8")

    with pytest.raises(click.ClickException) as excinfo:
        codex_target.TARGET.install(Scope.PROJECT, repo_path=repo)

    assert "no changes were written" in str(excinfo.value)
    assert config_path.read_text(encoding="utf-8") == poisoned


def test_marker_block_remove_refuses_what_upsert_refuses(tmp_path: Path) -> None:
    """Guarding only the write half still lets uninstall eat the sentence.

    ``remove`` strips *every* matched span, so on one real block plus a
    sentence quoting both markers it deletes the middle of the sentence — the
    exact file shape ``upsert`` refuses.
    """
    doc = tmp_path / "AGENTS.md"
    original = (
        "Docs: we manage the text between <!--S--> and <!--E-->.\n\n<!--S-->\nBODY\n<!--E-->\n"
    )
    doc.write_text(original, encoding="utf-8", newline="\n")

    assert marker_block.remove(doc, "<!--S-->", "<!--E-->") is False
    assert doc.read_text(encoding="utf-8") == original


def test_atomic_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config.json"
    target.parent.mkdir()
    json_merge.atomic_write_text(target, '{"a": 1}\n')

    assert target.read_text(encoding="utf-8").startswith("{")
    assert [p.name for p in target.parent.iterdir()] == ["config.json"]


def test_atomic_write_does_not_create_parent_directories(tmp_path: Path) -> None:
    """A missing parent stays a loud failure rather than a silently-created tree.

    ``.mcp.json`` is the one writer that never created its parent, so a
    nonexistent repo path raised. Creating it here would turn that into a
    success that writes config into a directory nobody asked for.
    """
    missing = tmp_path / "no-such-repo" / "config.json"
    with pytest.raises(OSError):
        json_merge.atomic_write_text(missing, "{}\n")
    assert not missing.parent.exists()


def test_atomic_write_preserves_an_existing_files_permissions(tmp_path: Path) -> None:
    """A temp-file rename installs the temp's umask-derived mode; restore the original."""
    target = tmp_path / "config.json"
    target.write_text("{}\n", encoding="utf-8")
    before = target.stat().st_mode

    json_merge.atomic_write_text(target, '{"a": 1}\n')

    assert target.stat().st_mode == before


def test_atomic_write_preserves_the_original_when_the_write_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """A crash mid-write must not truncate the file it was replacing."""
    target = tmp_path / "config.json"
    target.write_text("original", encoding="utf-8")

    def _explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _explode)
    with pytest.raises(OSError):
        json_merge.atomic_write_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_marker_block_round_trips_user_content_byte_for_byte(tmp_path: Path) -> None:
    """The property the managed block lives or dies by."""
    doc = tmp_path / "AGENTS.md"
    original = "# My notes\n\nSomething I wrote.\n"
    doc.write_text(original, encoding="utf-8", newline="\n")

    marker_block.upsert(doc, "\nmanaged\n", "<!--S-->", "<!--E-->")
    assert "Something I wrote." in doc.read_text(encoding="utf-8")

    marker_block.remove(doc, "<!--S-->", "<!--E-->")
    assert doc.read_text(encoding="utf-8") == original


def test_marker_block_upsert_is_idempotent(tmp_path: Path) -> None:
    doc = tmp_path / "AGENTS.md"
    assert marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->") is FileAction.CREATED
    assert marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->") is FileAction.UNCHANGED


def test_marker_block_refuses_an_orphaned_marker(tmp_path: Path) -> None:
    """A start with no end is left strictly alone, and said so.

    Every repair here loses something. Appending a fresh block leaves the stray
    start above it, so the next run's non-greedy ``start.*?end`` spans from the
    orphan to *our* end marker and eats whatever the user wrote in between.
    Before this, upsert reported "unchanged" and wrote nothing — technically
    safe, but it claimed the block was installed when it was absent.
    """
    doc = tmp_path / "AGENTS.md"
    original = "<!--S-->\nI deleted the end marker\n\nMy own paragraph.\n"
    doc.write_text(original, encoding="utf-8", newline="\n")

    assert marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->") is FileAction.KEPT
    assert doc.read_text(encoding="utf-8") == original


def test_marker_block_refuses_a_duplicated_block(tmp_path: Path) -> None:
    """Collapsing to the first copy is the trap, not the fix.

    It reads as safe — every copy is ours, so there is nothing of theirs to
    lose. But ``inspect`` counts marker occurrences, so it cannot tell two
    blocks from one block plus a sentence quoting both markers, and collapsing
    deletes the sentence.
    """
    doc = tmp_path / "AGENTS.md"
    original = (
        "# Notes\n\n<!--S-->managed<!--E-->\n\n"
        "Repowise owns the text between <!--S--> and <!--E-->, so leave it alone.\n"
    )
    doc.write_text(original, encoding="utf-8", newline="\n")

    assert marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->") is FileAction.KEPT
    assert doc.read_text(encoding="utf-8") == original


def test_marker_block_never_wedges_itself_on_a_nested_pair(tmp_path: Path) -> None:
    """A repair that leaves the file worse than it found it is not a repair.

    Rewriting the first match of ``S A S B E text E`` leaves a stray trailing
    end marker, which reads as orphaned forever after — so the managed block
    could never be updated again, by any command.
    """
    doc = tmp_path / "AGENTS.md"
    original = "<!--S-->a<!--S-->b<!--E-->keep me<!--E-->\n"
    doc.write_text(original, encoding="utf-8", newline="\n")

    assert marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->") is FileAction.KEPT
    assert doc.read_text(encoding="utf-8") == original


def test_marker_block_normalizes_a_crlf_file_whose_block_is_current(tmp_path: Path) -> None:
    """A CRLF file with an already-current block still gets normalized to LF.

    The subtle one. ``read_text`` collapses CRLF to LF in memory, so comparing
    decoded text says "identical" while the bytes on disk are not — and the
    upsert would return early, leaving the file CRLF where an unconditional
    write normalized it. That is a whole-file diff on any Windows checkout with
    ``core.autocrlf=true`` running ``repowise update``.
    """
    doc = tmp_path / "AGENTS.md"
    body = "\nmanaged\n"
    lf_content = f"# Notes\r\n\r\n<!--S-->{body}<!--E-->\n".replace("\n", "\r\n")
    doc.write_bytes(lf_content.encode("utf-8"))
    assert b"\r\n" in doc.read_bytes()

    action = marker_block.upsert(doc, body, "<!--S-->", "<!--E-->")

    assert action is FileAction.UPDATED
    assert b"\r\n" not in doc.read_bytes()
    # A second pass has nothing left to do.
    assert marker_block.upsert(doc, body, "<!--S-->", "<!--E-->") is FileAction.UNCHANGED


def test_marker_block_uses_lf_regardless_of_platform(tmp_path: Path) -> None:
    """These files are committed; their line endings must not depend on the OS."""
    doc = tmp_path / "AGENTS.md"
    marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->")
    assert b"\r\n" not in doc.read_bytes()


def test_marker_block_deletes_a_file_it_created(tmp_path: Path) -> None:
    """Install then uninstall round-trips to 'no file', not to a stub."""
    doc = tmp_path / "AGENTS.md"
    placeholder = "# AGENTS.md\n"
    doc.write_text(placeholder, encoding="utf-8", newline="\n")
    marker_block.upsert(doc, "\nbody\n", "<!--S-->", "<!--E-->")

    marker_block.remove(doc, "<!--S-->", "<!--E-->", delete_if_only=placeholder)
    assert not doc.exists()


def test_marker_block_names_the_malformed_states(tmp_path: Path) -> None:
    """Orphan and duplicate markers mean a user edited inside the block.

    Both are reported rather than silently repaired: guessing at the repair for
    an orphan marker is how you eat the paragraph that follows it.
    """
    doc = tmp_path / "AGENTS.md"

    assert marker_block.inspect(doc, "<!--S-->", "<!--E-->").state.value == "absent-file"

    doc.write_text("nothing here\n", encoding="utf-8")
    assert marker_block.inspect(doc, "<!--S-->", "<!--E-->").state.value == "absent"

    doc.write_text("<!--S-->body<!--E-->\n", encoding="utf-8")
    inspection = marker_block.inspect(doc, "<!--S-->", "<!--E-->")
    assert inspection.state.value == "present"
    assert inspection.body == "body"

    doc.write_text("<!--S-->a<!--E-->\n<!--S-->b<!--E-->\n", encoding="utf-8")
    assert marker_block.inspect(doc, "<!--S-->", "<!--E-->").state.value == "duplicated"

    doc.write_text("<!--S-->dangling\n", encoding="utf-8")
    assert marker_block.inspect(doc, "<!--S-->", "<!--E-->").state.value == "orphaned"


# ---------------------------------------------------------------------------
# Hermes
#
# The first target whose config is YAML, and the first whose two config keys
# invite a second write that would be actively harmful. Both properties get
# their own tests, because neither is visible in a fresh-install golden.
# ---------------------------------------------------------------------------


@pytest.fixture
def hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _hermes():
    from repowise.cli.agent_targets.targets import hermes

    return hermes


def _seed(home: Path, body: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    config.write_bytes(body.encode("utf-8"))
    return config


def test_hermes_home_follows_the_hosts_own_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HERMES_HOME wins, then the platform default, and Windows is not ``~/.hermes``.

    The Windows branch is the one worth pinning. An entry written to
    ``~/.hermes`` on Windows is not broken, it is invisible, and nothing about
    the install would report a problem.
    """
    hermes = _hermes()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "explicit"))
    assert hermes.hermes_home() == tmp_path / "explicit"

    # Blank and whitespace-only are not a prefix: joining them resolves to the
    # filesystem root, or to a relative path named " ".
    monkeypatch.setenv("HERMES_HOME", "   ")
    monkeypatch.setattr(hermes.sys, "platform", "linux")
    assert hermes.hermes_home() == Path.home() / ".hermes"

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(hermes.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert hermes.hermes_home() == tmp_path / "AppData" / "Local" / "hermes"
    assert hermes.hermes_home() != Path.home() / ".hermes"


def test_hermes_writes_the_server_entry_and_is_idempotent(hermes_home: Path) -> None:
    import yaml

    hermes = _hermes()
    target = get_target("hermes")

    first = target.install(Scope.USER)
    assert [written.action for written in first.files] == [FileAction.CREATED]

    config = hermes.config_path()
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    entry = doc["mcp_servers"]["repowise"]
    # No repo path: one per-machine registration serves every repo, and the
    # server resolves the repo it was launched in.
    assert entry["args"] == ["mcp", "--transport", "stdio"]
    # Keys Hermes does not document must not be invented, and ``enabled``
    # defaults to true so writing it only creates something to overwrite.
    assert set(entry) == {"command", "args"}

    second = target.install(Scope.USER)
    assert [written.action for written in second.files] == [FileAction.UNCHANGED]


def test_hermes_does_not_turn_a_permissive_config_into_an_allowlist(
    hermes_home: Path,
) -> None:
    """The single most consequential thing this target does not do.

    Hermes exposes every enabled MCP server to a platform unless that
    platform's toolset list already names one, at which point the list becomes
    an allowlist. Adding ``repowise`` to a list that names none would flip the
    config onto the allowlist branch with exactly one entry and cut off every
    other MCP server the user had.
    """
    import yaml

    config = _seed(
        hermes_home,
        "mcp_servers:\n"
        "  github:\n"
        "    command: npx\n"
        "\n"
        "platform_toolsets:\n"
        "  cli: [hermes-cli]\n",
    )
    get_target("hermes").install(Scope.USER)

    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert doc["platform_toolsets"]["cli"] == ["hermes-cli"]
    assert "repowise" in doc["mcp_servers"]
    assert "github" in doc["mcp_servers"]


def test_hermes_joins_a_list_that_is_already_an_mcp_allowlist(hermes_home: Path) -> None:
    """The mirror case, where omitting the entry is the silent failure.

    The list already names an MCP server, so Hermes is filtering on it, and a
    repowise absent from it would be registered and never reach the CLI.
    """
    import yaml

    config = _seed(
        hermes_home,
        "mcp_servers:\n"
        "  github:\n"
        "    command: npx\n"
        "\n"
        "platform_toolsets:\n"
        "  cli:\n"
        "  - hermes-cli\n"
        "  - github\n",
    )
    get_target("hermes").install(Scope.USER)

    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert doc["platform_toolsets"]["cli"] == ["hermes-cli", "github", "repowise"]


def test_hermes_ignores_an_allowlist_naming_only_a_disabled_server(
    hermes_home: Path,
) -> None:
    """Membership is tested against *enabled* servers, exactly as the host tests it.

    A list naming a server the user has since switched off is not an allowlist
    to Hermes, so joining it would create the restriction the test above exists
    to prevent.
    """
    import yaml

    config = _seed(
        hermes_home,
        "mcp_servers:\n"
        "  github:\n"
        "    command: npx\n"
        "    enabled: false\n"
        "\n"
        "platform_toolsets:\n"
        "  cli: [hermes-cli, github]\n",
    )
    get_target("hermes").install(Scope.USER)

    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert doc["platform_toolsets"]["cli"] == ["hermes-cli", "github"]


def test_hermes_reports_the_no_mcp_sentinel_rather_than_writing_past_it(
    hermes_home: Path,
) -> None:
    """``no_mcp`` turns every MCP server off for the platform.

    The entry is still written, because the sentinel is a per-platform switch
    the user can flip back, but a silent success here looks exactly like a
    working install right up to the point where no tools appear.
    """
    import yaml

    config = _seed(
        hermes_home,
        "mcp_servers:\n"
        "  github:\n"
        "    command: npx\n"
        "\n"
        "platform_toolsets:\n"
        "  cli: [hermes-cli, no_mcp]\n",
    )
    result = get_target("hermes").install(Scope.USER)

    assert any("no_mcp" in note for note in result.notes)
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert doc["platform_toolsets"]["cli"] == ["hermes-cli", "no_mcp"]


def test_hermes_preserves_comments_anchors_and_line_endings(hermes_home: Path) -> None:
    """The reason this target splices rather than reserializing.

    A ``safe_load``/``safe_dump`` round-trip loses all three: the comments
    outright, and the anchors by expanding them into the values they stand for,
    which is a change no diff of the parsed data would show.
    """
    original = (
        "# my hermes config\r\n"
        "defaults: &d\r\n"
        "  timeout: 30\r\n"
        "model:\r\n"
        "  <<: *d\r\n"
        "  name: gpt-5  # the good one\r\n"
    )
    config = _seed(hermes_home, original)
    get_target("hermes").install(Scope.USER)

    text = config.read_bytes().decode("utf-8")
    assert "# my hermes config" in text
    assert "&d" in text
    assert "<<: *d" in text
    assert "# the good one" in text
    assert "\r\n" in text
    assert "\n" not in text.replace("\r\n", "")


def test_hermes_does_not_convert_an_lf_config_to_crlf(hermes_home: Path) -> None:
    """The discriminating half of line-ending preservation, and it only shows on Windows.

    A CRLF fixture cannot prove this here: the platform translation an ordinary
    config write uses *also* emits CRLF on Windows, so that test stays green
    against a writer that ignores the file entirely. An LF file is the case
    where the two answers differ, and turning every line of a user's config
    over on a three-line edit is a whole-file diff rather than a cosmetic one.
    """
    config = _seed(hermes_home, "# my hermes config\nmodel:\n  name: gpt-5\n")
    get_target("hermes").install(Scope.USER)

    assert b"\r\n" not in config.read_bytes()


def test_hermes_round_trips_a_seeded_config_byte_for_byte(hermes_home: Path) -> None:
    original = (
        "# my hermes config\n"
        "model:\n"
        "  name: gpt-5  # the good one\n"
        "\n"
        "platform_toolsets:\n"
        "  cli: [hermes-cli]\n"
    )
    config = _seed(hermes_home, original)
    target = get_target("hermes")

    target.install(Scope.USER)
    assert config.read_bytes() != original.encode("utf-8")

    target.uninstall(Scope.USER)
    assert config.read_bytes() == original.encode("utf-8")


def test_hermes_declines_a_config_it_cannot_splice_safely(hermes_home: Path) -> None:
    """A repeated top-level key is a shape the line matching cannot get right.

    The search finds the first ``mcp_servers:`` while the parser keeps the
    last, so an edit lands in a block the document does not use. It is caught
    by re-parsing the spliced text and comparing it against the document the
    target meant to write, which is what makes line-based surgery safe to ship:
    a mis-fired splice becomes a decline, never a corrupted file.
    """
    original = (
        "mcp_servers:\n"
        "  github:\n"
        "    command: gh\n"
        "mcp_servers:\n"
        "  other:\n"
        "    command: npx\n"
    )
    config = _seed(hermes_home, original)

    result = get_target("hermes").install(Scope.USER)

    assert [written.action for written in result.files] == [FileAction.KEPT]
    assert config.read_bytes() == original.encode("utf-8")
    assert any("print-config hermes" in note for note in result.notes)


def test_hermes_declines_unparseable_and_undecodable_configs(hermes_home: Path) -> None:
    """``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``.

    Nothing wraps ``install``, so an escape here aborts the whole run after
    other agents' configs have been written.
    """
    hermes = _hermes()
    target = get_target("hermes")

    broken = _seed(hermes_home, "model: [unclosed\n")
    result = target.install(Scope.USER)
    assert [written.action for written in result.files] == [FileAction.KEPT]
    assert broken.read_bytes() == b"model: [unclosed\n"
    assert target.doctor().status.value == "broken"

    (hermes_home / "config.yaml").write_bytes(b"model:\n  name: caf\xe9\n")
    result = target.install(Scope.USER)
    assert [written.action for written in result.files] == [FileAction.KEPT]
    assert (hermes_home / "config.yaml").read_bytes() == b"model:\n  name: caf\xe9\n"
    # detect is contracted never to raise, whatever the file holds.
    assert hermes.detect() == []


def test_hermes_leaves_a_remote_entry_alone(hermes_home: Path) -> None:
    """Hermes prefers a ``url`` over a ``command`` even when both are present.

    So merging our command into an entry that has a url would leave a
    registration that reads as repointed and still never launches us. The
    shared ``is_remote_entry`` helper keys on a declared ``type`` field, which
    this host does not have, and its fallback limb would call this entry local.
    """
    original = (
        "mcp_servers:\n"
        "  repowise:\n"
        '    url: "https://example.invalid/mcp"\n'
        "    command: repowise\n"
    )
    config = _seed(hermes_home, original)

    result = get_target("hermes").install(Scope.USER)

    assert [written.action for written in result.files] == [FileAction.KEPT]
    assert config.read_bytes() == original.encode("utf-8")
    assert any("remote server" in note for note in result.notes)


def test_hermes_keeps_the_keys_a_user_added_to_our_entry(hermes_home: Path) -> None:
    """Generated keys win so a moved install takes effect; everything else survives.

    ``enabled: false`` is the one most worth keeping: parking a server without
    deleting it is the documented use of that flag, and re-enabling it on every
    ``agents refresh`` would undo a deliberate choice.
    """
    import yaml

    config = _seed(
        hermes_home,
        "mcp_servers:\n"
        "  repowise:\n"
        "    command: /old/path/repowise\n"
        "    args: [mcp]\n"
        "    env:\n"
        "      MY_KEY: secret\n"
        "    timeout: 300\n"
        "    enabled: false\n",
    )
    get_target("hermes").install(Scope.USER)

    entry = yaml.safe_load(config.read_text(encoding="utf-8"))["mcp_servers"]["repowise"]
    assert entry["command"] != "/old/path/repowise"
    assert entry["args"] == ["mcp", "--transport", "stdio"]
    assert entry["env"] == {"MY_KEY": "secret"}
    assert entry["timeout"] == 300
    assert entry["enabled"] is False


def test_hermes_uninstall_keeps_a_config_file_it_did_not_create(hermes_home: Path) -> None:
    """``config.yaml`` is the host's own file, so an empty one is not proof it is ours.

    The unambiguous signal is the directory: Hermes seeds ``SOUL.md`` and ten
    subdirectories into its home on every config load, so a lone ``config.yaml``
    cannot have come from it, and anything else beside it means the file stays.
    """
    hermes = _hermes()
    config = _seed(hermes_home, "")
    (hermes_home / "SOUL.md").write_text("you are helpful\n", encoding="utf-8")

    target = get_target("hermes")
    target.install(Scope.USER)
    target.uninstall(Scope.USER)

    assert config.exists()
    assert hermes.hermes_home().exists()
    assert config.read_text(encoding="utf-8").strip() == ""


def test_hermes_uninstall_removes_a_home_it_created(hermes_home: Path) -> None:
    """Otherwise our own residue keeps the agent pre-ticked forever.

    ``is_present`` reads this directory as evidence the user has Hermes, so an
    ``agents add`` followed by an ``agents remove`` would leave the machine
    looking like a Hermes machine when it never was.
    """
    hermes = _hermes()
    target = get_target("hermes")

    assert not hermes.hermes_home().exists()
    target.install(Scope.USER)
    assert hermes.config_path().exists()

    target.uninstall(Scope.USER)
    assert not hermes.config_path().exists()
    assert not hermes.hermes_home().exists()


def test_hermes_project_scope_manages_the_agents_md_block(
    hermes_home: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_bytes(b"# House rules\n\nBe nice.\n")
    target = get_target("hermes")

    first = target.install(Scope.PROJECT, repo_path=repo)
    assert [written.action for written in first.files] == [FileAction.UPDATED]
    second = target.install(Scope.PROJECT, repo_path=repo)
    assert [written.action for written in second.files] == [FileAction.UNCHANGED]

    target.uninstall(Scope.PROJECT, repo_path=repo)
    assert (repo / "AGENTS.md").read_bytes() == b"# House rules\n\nBe nice.\n"


def test_hermes_does_not_write_the_file_the_host_prefers(
    hermes_home: Path, tmp_path: Path
) -> None:
    """Hermes loads one project context file, and ``HERMES.md`` outranks ``AGENTS.md``.

    Writing the host-named file into a repo that already has an ``AGENTS.md``
    would take precedence over it and suppress the repo's real instructions.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    get_target("hermes").install(Scope.PROJECT, repo_path=repo)

    assert (repo / "AGENTS.md").exists()
    assert not (repo / "HERMES.md").exists()
    assert not (repo / ".hermes.md").exists()


def test_hermes_project_registration_requires_the_global_one(
    hermes_home: Path, tmp_path: Path
) -> None:
    """A managed block on its own must not report Hermes as wired.

    ``AGENTS.md`` is written by two other agents, so reading the block alone as
    "Hermes is wired here" would report Hermes as an owner of that file on a
    machine that has never had Hermes, and ``other_managers_of`` asks exactly
    that before letting ``agents remove --target=codex`` strip the block.
    """
    hermes = _hermes()
    repo = tmp_path / "repo"
    repo.mkdir()
    target = get_target("hermes")

    target.install(Scope.PROJECT, repo_path=repo)
    assert hermes.detect(repo) == []

    target.install(Scope.USER)
    assert [registration.scope for registration in hermes.detect(repo)] == [
        Scope.USER,
        Scope.PROJECT,
    ]


def test_hermes_and_codex_do_not_strip_the_shared_block_from_each_other(
    hermes_home: Path, tmp_path: Path
) -> None:
    """Three targets now manage ``AGENTS.md``, and the guard is over the registry.

    Driven through both targets rather than by calling the helper by hand, so
    deleting either side's guard fails this.

    One scenario per direction, each on its own repo. Chaining them used to work
    on a single repo and no longer does, for a reason worth recording: Codex's
    project uninstall now removes ``.codex/config.toml``, so a removed Codex
    stops being detected as wired. Before that, its leftover config kept
    answering "still an owner" long after it had been removed, and the second
    half of a chained test was really asserting on that leftover. The block
    surviving both agents' removal was the bug, not the contract.
    """
    hermes_target = get_target("hermes")
    codex_target = get_target("codex")
    hermes_target.install(Scope.USER)

    # Codex removed while Hermes is still wired.
    codex_first = tmp_path / "codex-first"
    codex_first.mkdir()
    codex_target.install(Scope.PROJECT, repo_path=codex_first)
    hermes_target.install(Scope.PROJECT, repo_path=codex_first)
    removed_codex = codex_target.uninstall(Scope.PROJECT, repo_path=codex_first)
    kept_codex = [w for w in removed_codex.files if w.action is FileAction.KEPT]
    assert kept_codex
    assert any("Hermes" in (w.reason or "") for w in kept_codex)
    assert any("Hermes" in note for note in removed_codex.notes)
    assert "REPOWISE_DISTILL:START" in (codex_first / "AGENTS.md").read_text(encoding="utf-8")

    # Hermes removed while Codex is still wired.
    hermes_first = tmp_path / "hermes-first"
    hermes_first.mkdir()
    codex_target.install(Scope.PROJECT, repo_path=hermes_first)
    hermes_target.install(Scope.PROJECT, repo_path=hermes_first)
    removed_hermes = hermes_target.uninstall(Scope.PROJECT, repo_path=hermes_first)
    kept_hermes = [w for w in removed_hermes.files if w.action is FileAction.KEPT]
    assert kept_hermes
    assert any("Codex" in (w.reason or "") for w in kept_hermes)
    assert any("Codex" in note for note in removed_hermes.notes)
    assert "REPOWISE_DISTILL:START" in (hermes_first / "AGENTS.md").read_text(encoding="utf-8")

    # Removing the second agent in a *separate* invocation does not free the
    # block, and that is a known limitation rather than an accident. Hermes's
    # project-scope detection reads the very block that was kept for Codex, so
    # a Hermes removed a command ago still answers "still an owner" and Codex
    # keeps the block for it. `registry.removing` breaks exactly this cycle, but
    # only within one command: `agents remove --target=codex,hermes` and
    # `repowise uninstall` both batch and both clear the block. Pinned here so
    # the behaviour is a recorded decision rather than a surprise.
    second = codex_target.uninstall(Scope.PROJECT, repo_path=hermes_first)
    assert FileAction.KEPT in [w.action for w in second.files]
    assert "REPOWISE_DISTILL:START" in (hermes_first / "AGENTS.md").read_text(encoding="utf-8")


def test_hermes_batch_removal_does_not_deadlock_on_the_shared_block(
    hermes_home: Path, tmp_path: Path
) -> None:
    """Removing both at once must not have each keep the file for the other."""
    from repowise.cli.agent_targets.registry import removing

    repo = tmp_path / "repo"
    repo.mkdir()
    hermes_target = get_target("hermes")
    codex_target = get_target("codex")

    codex_target.install(Scope.PROJECT, repo_path=repo)
    hermes_target.install(Scope.USER)
    hermes_target.install(Scope.PROJECT, repo_path=repo)

    with removing(["codex", "hermes"]):
        codex_target.uninstall(Scope.PROJECT, repo_path=repo)
        hermes_target.uninstall(Scope.PROJECT, repo_path=repo)

    assert not (repo / "AGENTS.md").exists()


def test_hermes_print_config_parses_as_the_shape_the_host_reads(hermes_home: Path) -> None:
    """It also must not touch the filesystem, and must not print the toolset key.

    Printing ``platform_toolsets`` would invite the user to paste the exact
    change this target declines to make, and a snippet cannot carry the
    condition under which it is correct.
    """
    import yaml

    snippet = get_target("hermes").print_config(Scope.USER)
    assert not hermes_home.exists()

    doc = yaml.safe_load(snippet)
    assert set(doc) == {"mcp_servers"}
    assert doc["mcp_servers"]["repowise"]["args"] == ["mcp", "--transport", "stdio"]


def test_hermes_describe_paths_names_what_each_scope_writes(
    hermes_home: Path, tmp_path: Path
) -> None:
    target = get_target("hermes")
    repo = tmp_path / "repo"

    assert target.describe_paths(Scope.USER) == [str(hermes_home / "config.yaml")]
    assert target.describe_paths(Scope.PROJECT, repo_path=repo) == [str(repo / "AGENTS.md")]


def test_hermes_doctor_distinguishes_absent_from_damaged(hermes_home: Path) -> None:
    target = get_target("hermes")
    assert target.doctor().status.value == "not-installed"

    config = _seed(hermes_home, "model: [unclosed\n")
    report = target.doctor()
    assert report.status.value == "broken"
    # Refresh skips what it cannot detect and install declines for the same
    # reason this failed, so --repair has nothing to offer.
    assert report.repairable is False

    # Install declines against a file it cannot read, so the row stays broken
    # rather than flipping to ok on a write that did not happen.
    target.install(Scope.USER)
    assert target.doctor().status.value == "broken"

    config.unlink()
    target.install(Scope.USER)
    assert target.doctor().status.value == "ok"


def test_hermes_uninstall_keeps_a_config_holding_only_the_users_comments(
    hermes_home: Path,
) -> None:
    """An empty document is not an empty file, and the parse cannot tell them apart.

    A ``config.yaml`` that is nothing but the user's comments parses to ``{}``,
    and so does one whose only server is commented out with a note about why it
    is parked. Deleting either on the strength of the parse destroys a file the
    user wrote, reports it as ``removed``, and says nothing. This is the
    sole-occupant case too, so the directory test alone does not save it.
    """
    target = get_target("hermes")

    # Comments at top level only. After the removal the document really is
    # empty, so this is the case that isolates the text test: nothing else
    # stops the delete.
    bare = "# my hermes config, managed by chezmoi\n# model:\n#   name: gpt-5\n"
    config = _seed(hermes_home, bare)
    assert [entry.name for entry in hermes_home.iterdir()] == ["config.yaml"]

    target.install(Scope.USER)
    target.uninstall(Scope.USER)

    assert config.exists()
    assert config.read_bytes() == bare.encode("utf-8")
    assert hermes_home.exists()

    # Comments inside the section we own. Here the section header survives with
    # them, so the document is not empty either, and both guards have to hold
    # for the round trip to be exact.
    parked = (
        "mcp_servers:\n"
        "  # github parked 2026-08-01, re-enable after the token rotates\n"
        "  # github:\n"
        "  #   command: gh\n"
    )
    config.write_bytes(parked.encode("utf-8"))
    target.install(Scope.USER)
    target.uninstall(Scope.USER)

    assert config.exists()
    assert config.read_bytes() == parked.encode("utf-8")


def test_hermes_installs_into_a_section_header_with_nothing_under_it(
    hermes_home: Path,
) -> None:
    """``mcp_servers:`` with only a comment under it parses to ``None``, not ``{}``.

    ``setdefault`` is the obvious way to reach that key and it does not replace
    a present-but-null one, so this declined permanently with a note calling a
    valid file invalid, and doctor then handed back the command that had just
    refused. Hermes coerces the same key before reading it.
    """
    import yaml

    config = _seed(hermes_home, "model: gpt\nmcp_servers:\n  # servers go here\n")
    result = get_target("hermes").install(Scope.USER)

    assert [written.action for written in result.files] == [FileAction.UPDATED]
    assert result.notes == []
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "repowise" in doc["mcp_servers"]
    assert "# servers go here" in config.read_text(encoding="utf-8")


def test_hermes_installs_into_a_flow_style_section(hermes_home: Path) -> None:
    """``mcp_servers: {}`` is legal and used to be an unfixable decline.

    The whole cycle, because keeping the inline style on write and not teaching
    removal about it left uninstall reporting ``kept`` and doing nothing.
    """
    import yaml

    original = "model: gpt\nmcp_servers: {}\n"
    config = _seed(hermes_home, original)
    target = get_target("hermes")

    result = target.install(Scope.USER)
    assert [written.action for written in result.files] == [FileAction.UPDATED]
    doc = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "repowise" in doc["mcp_servers"]
    assert doc["model"] == "gpt"

    assert [written.action for written in target.install(Scope.USER).files] == [
        FileAction.UNCHANGED
    ]

    removed = target.uninstall(Scope.USER)
    assert [written.action for written in removed.files] == [FileAction.REMOVED]
    assert config.read_bytes() == original.encode("utf-8")


def test_hermes_survives_a_byte_order_mark(hermes_home: Path) -> None:
    """A BOM hides the first key from a search anchored at column zero.

    The edit then appends a second copy of a section that was already there,
    the duplicate makes the merged text parse to something else, and install
    declines forever on a file both PyYAML and Hermes read fine. The mark is
    carried across rather than dropped, so the round trip is byte exact.
    """
    import yaml

    original = "﻿mcp_servers:\n  github:\n    command: gh\n"
    config = _seed(hermes_home, original)
    target = get_target("hermes")

    result = target.install(Scope.USER)
    assert [written.action for written in result.files] == [FileAction.UPDATED]

    text = config.read_bytes().decode("utf-8")
    assert text.startswith("﻿")
    doc = yaml.safe_load(text)
    assert set(doc["mcp_servers"]) == {"github", "repowise"}

    target.uninstall(Scope.USER)
    assert config.read_bytes() == original.encode("utf-8")


def test_hermes_round_trips_an_allowlist_config_through_the_cli_path(
    hermes_home: Path,
) -> None:
    """The allowlist branch, exercised through install and uninstall.

    The other round-trip test seeds ``cli: [hermes-cli]``, which does not name
    an MCP server and so never reaches the toolset write at all. Without this
    one, the inline-list rendering is covered only by calling the helper by
    hand, and the failure it guards against shows up on uninstall.
    """
    original = (
        "mcp_servers:\n"
        "  othersrv:            # a server I already had\n"
        "    command: npx\n"
        "\n"
        "platform_toolsets:\n"
        "  cli: [hermes-cli, othersrv]\n"
    )
    config = _seed(hermes_home, original)
    target = get_target("hermes")

    target.install(Scope.USER)
    text = config.read_text(encoding="utf-8")
    assert "  cli: [hermes-cli, othersrv, repowise]\n" in text
    assert "# a server I already had" in text

    assert [written.action for written in target.install(Scope.USER).files] == [
        FileAction.UNCHANGED
    ]

    target.uninstall(Scope.USER)
    assert config.read_bytes() == original.encode("utf-8")


# ---------------------------------------------------------------------------
# yaml_merge
# ---------------------------------------------------------------------------


def test_yaml_merge_treats_a_same_indent_block_list_as_part_of_its_key() -> None:
    """The dumper writes block sequences at the parent key's own indent.

    Reading the first ``- `` line as the next sibling truncates the block after
    one line, and the edit then lands above items that are still there.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    text = "platform_toolsets:\n  cli:\n  - hermes-cli\n  - github\nother: 1\n"
    merged = yaml_merge.set_child(text, "platform_toolsets", "cli", ["hermes-cli", "x"])

    assert yaml_merge.load_mapping(merged) == {
        "platform_toolsets": {"cli": ["hermes-cli", "x"]},
        "other": 1,
    }
    assert "github" not in merged


def test_yaml_merge_verify_refuses_a_splice_that_missed() -> None:
    """The guard that makes line surgery safe: intent is checked, not assumed."""
    from repowise.cli.agent_targets.formats import yaml_merge

    yaml_merge.verify("a: 1\n", {"a": 1})
    with pytest.raises(ValueError):
        yaml_merge.verify("a: 1\n", {"a": 2})


def test_yaml_merge_rejects_what_is_not_a_mapping() -> None:
    """A ``yaml.YAMLError`` is not a ``ValueError``, so it would escape every handler."""
    from repowise.cli.agent_targets.formats import yaml_merge

    assert yaml_merge.load_mapping("") == {}
    with pytest.raises(ValueError):
        yaml_merge.load_mapping("- a\n- b\n")
    with pytest.raises(ValueError):
        yaml_merge.load_mapping("a: [unclosed\n")


def test_yaml_merge_detects_the_newline_to_write_back_with() -> None:
    from repowise.cli.agent_targets.formats import yaml_merge

    assert yaml_merge.detect_newline("a: 1\nb: 2\n") == "\n"
    assert yaml_merge.detect_newline("a: 1\r\nb: 2\r\n") == "\r\n"


def test_yaml_merge_removes_the_parent_once_it_is_empty() -> None:
    """A bare ``parent:`` parses as ``None``, which is a different document."""
    from repowise.cli.agent_targets.formats import yaml_merge

    text = "mcp_servers:\n  repowise:\n    command: x\nmodel: gpt\n"
    merged, section = yaml_merge.remove_child(text, "mcp_servers", "repowise")

    assert section is yaml_merge.ABSENT
    assert yaml_merge.load_mapping(merged) == {"model": "gpt"}
    assert "mcp_servers" not in merged


def test_yaml_merge_keeps_a_section_that_still_holds_the_users_comments() -> None:
    """Comments are content, and the parse cannot see them.

    A section left holding only comments must survive, which means the header
    survives with it and the key parses to ``None`` rather than going away. The
    caller is told which happened because it cannot work it out from the text,
    and ``verify`` cannot catch getting it wrong: deleting the comments leaves
    the same document.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    text = (
        "mcp_servers:\n"
        "  # github parked 2026-08-01, re-enable after the token rotates\n"
        "  # github:\n"
        "  #   command: gh\n"
        "  repowise:\n"
        "    command: x\n"
    )
    merged, section = yaml_merge.remove_child(text, "mcp_servers", "repowise")

    # The header stays with the comments, so the key holds ``None`` rather than
    # being gone. ``ABSENT`` is a sentinel precisely so those two stay distinct.
    assert section is None
    assert section is not yaml_merge.ABSENT
    assert "token rotates" in merged
    assert "#   command: gh" in merged
    assert yaml_merge.load_mapping(merged) == {"mcp_servers": None}


def test_yaml_merge_can_add_a_child_to_a_flow_mapping_parent() -> None:
    """``mcp_servers: {}`` is an ordinary way to write an empty section.

    A block child cannot be spliced under a value that is already closed, so
    this used to produce unparseable text, get refused by ``verify``, and
    surface "not valid YAML" about a file that is entirely valid, with no way
    forward for the user.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    empty = yaml_merge.set_child(
        "model: gpt\nmcp_servers: {}\n", "mcp_servers", "repowise", {"command": "x"}
    )
    assert yaml_merge.load_mapping(empty) == {
        "model": "gpt",
        "mcp_servers": {"repowise": {"command": "x"}},
    }

    populated = yaml_merge.set_child(
        "mcp_servers: {github: {command: gh}}\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )
    assert yaml_merge.load_mapping(populated) == {
        "mcp_servers": {"github": {"command": "gh"}, "repowise": {"command": "x"}}
    }


def test_yaml_merge_flow_parent_edits_only_its_own_line() -> None:
    """The parent's range runs to the next top-level key, so replacing it is wrong.

    Everything between the inline value and the next section is blank lines and
    the user's comments, and splicing over the whole range deleted them. Same
    mistake the block path's walk-back exists to avoid, on a path that did not
    inherit the rule, and invisible to ``verify`` because the document is
    identical either way.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    merged = yaml_merge.set_child(
        "mcp_servers: {}\n\n# ---- model settings, do not remove ----\nmodel: a\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )

    assert "# ---- model settings, do not remove ----" in merged
    assert yaml_merge.load_mapping(merged) == {
        "mcp_servers": {"repowise": {"command": "x"}},
        "model": "a",
    }


def test_yaml_merge_does_not_read_a_hash_inside_an_inline_value_as_a_comment() -> None:
    """``#`` inside a plain scalar is data, and a nearby helper cannot tell.

    The scalar-line helper guards only against quoting, so given
    ``{a: {args: [-c, foo#bar]}}`` it returned ``#bar]}}`` and wrote that
    fragment of the user's own config into their file as a comment on the
    section header. The document is unchanged, so nothing downstream noticed.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    merged = yaml_merge.set_child(
        "mcp_servers: {a: {command: sh, args: [-c, foo#bar]}}\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )

    # Exactly one hash survives, the one that is part of the user's argument.
    # A second means a fragment of their value was copied out into a comment.
    assert merged.count("#") == 1
    assert "#bar]}}" not in merged.replace("foo#bar", "")
    assert yaml_merge.load_mapping(merged) == {
        "mcp_servers": {
            "a": {"command": "sh", "args": ["-c", "foo#bar"]},
            "repowise": {"command": "x"},
        }
    }

    # A real trailing comment survives, including one that mentions a bracket.
    # Searching backwards from the last bracket looks like the same answer and
    # reads the brace in "e.g. {a: b}" as the end of the value, throwing the
    # comment away, so both limbs are here.
    for comment in ("# my servers", "# e.g. {name: {command: x}}", "# note with ] bracket"):
        kept = yaml_merge.set_child(
            f"mcp_servers: {{}}  {comment}\n",
            "mcp_servers",
            "repowise",
            {"command": "x"},
        )
        assert comment in kept


def test_yaml_merge_finds_the_comment_boundary_the_way_the_parser_does() -> None:
    """Three hand-written rules were tried and each was wrong its own way.

    An apostrophe in a plain scalar is the case that defeats a quote tracker:
    ``note: don't`` leaves it stuck open, every bracket after it is miscounted,
    and the next hash in the data is returned as a comment. Which way it then
    goes is a coin toss, so both are pinned here: a fragment of the value must
    never be copied out into a comment, and a real comment must never be lost.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    def merged(line: str) -> str:
        return yaml_merge.set_child(
            line + "\n", "mcp_servers", "repowise", {"command": "R"}
        )

    # No comment here at all: the hash lives inside a quoted scalar.
    fragment = merged("mcp_servers: {a: don't, b: 'x}y # z'}")
    assert "#" not in fragment.replace("# z", "")
    assert yaml_merge.load_mapping(fragment)["mcp_servers"]["b"] == "x}y # z"

    # A real comment survives an apostrophe, and an escaped quote, before it.
    for line in (
        "mcp_servers: {a: {command: gh, args: [don't]}}  # keep me",
        'mcp_servers: {a: {command: "a\\"b"}}  # keep me',
    ):
        assert "# keep me" in merged(line)


def test_yaml_merge_refuses_a_wrapped_value_holding_a_comment() -> None:
    """A wrapped value is re-rendered onto one line, so a comment inside it has
    nowhere to go. Only the one following the value is recoverable, so rather
    than eat the rest the value is left alone. The uncommented wrapped case is
    the common one and still works.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    commented = (
        "mcp_servers: {github: {command: gh},   # first server\n"
        "              other: {command: o}}\n"
        "model: a\n"
    )
    assert (
        yaml_merge.set_child(commented, "mcp_servers", "repowise", {"command": "R"})
        == commented
    )


def test_yaml_merge_matches_a_quoted_key_at_both_levels() -> None:
    """Teaching one of the two searches about quoting was worse than neither.

    The child search learned it first, and the parent search left behind sent a
    quoted section down the append path, where the block it added was a
    duplicate key and the write then refused for good.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    for spelling in ('"mcp_servers"', "'mcp_servers'"):
        merged = yaml_merge.set_child(
            f"{spelling}:\n  github:\n    command: gh\n",
            "mcp_servers",
            "repowise",
            {"command": "R"},
        )
        assert merged.count("mcp_servers") == 1
        assert sorted(yaml_merge.load_mapping(merged)["mcp_servers"]) == [
            "github",
            "repowise",
        ]


def test_yaml_merge_handles_an_inline_value_that_wraps_across_lines() -> None:
    """The shortest slice that parses is the value.

    An unbalanced flow collection does not parse, so the first slice that does
    is the whole of it. Reading only the first line looks equivalent and turns
    a wrapped value from a working install into a permanent refusal.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    merged = yaml_merge.set_child(
        "mcp_servers: {github: {command: gh},\n              other: {command: o}}\nmodel: a\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )

    assert yaml_merge.load_mapping(merged) == {
        "mcp_servers": {
            "github": {"command": "gh"},
            "other": {"command": "o"},
            "repowise": {"command": "x"},
        },
        "model": "a",
    }


def test_yaml_merge_refuses_an_inline_value_carrying_an_anchor() -> None:
    """Adding a child re-renders the whole inline value, and anchors do not survive.

    The dumper writes them back under generated names, so a factoring the user
    wrote is replaced by ``&id001``. Everything else re-rendering normalises is
    cosmetic; this one is the damage the module refuses to do to a whole file,
    so it refuses it here too.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    original = "mcp_servers: {a: &base {command: gh}, b: *base}\n"
    assert (
        yaml_merge.set_child(original, "mcp_servers", "repowise", {"command": "x"})
        == original
    )

    # A star inside an ordinary scalar is not an alias and must not be refused.
    shell = "mcp_servers: {a: {command: sh, args: [-c, 'ls *']}}\n"
    merged = yaml_merge.set_child(shell, "mcp_servers", "repowise", {"command": "x"})
    assert "repowise" in yaml_merge.load_mapping(merged)["mcp_servers"]


def test_hermes_uninstall_declines_when_the_splice_missed(hermes_home: Path) -> None:
    """A removal that changed nothing must never report ``removed``.

    ``remove_child`` reports what it did, and reporting it by re-parsing its own
    output looks like the same answer: the caller feeds that value straight into
    the document it hands ``verify``, so both sides came from one parse of one
    string and the check could not fail. It then reported ``removed`` over a
    file that still held the entry, leaving repowise registered.

    The shape here is a repeated top-level key. The parser keeps the last, so
    the entry is genuinely registered, while the line search finds the first and
    edits a block the document does not use.
    """
    original = (
        "mcp_servers:\n"
        "  other:\n"
        "    command: y\n"
        "mcp_servers:\n"
        "  repowise:\n"
        "    command: x\n"
    )
    config = _seed(hermes_home, original)

    result = get_target("hermes").uninstall(Scope.USER)

    assert [written.action for written in result.files] == [FileAction.KEPT]
    assert config.read_bytes() == original.encode("utf-8")


def test_hermes_updates_a_quoted_key_in_place(hermes_home: Path) -> None:
    """Quoting a mapping key is ordinary YAML, and matching only the bare form
    was wrong in both directions: the write appended a second entry beside the
    quoted one, leaving a duplicate key the parser silently resolves to the
    last, and the removal then found neither and declined for good.
    """
    import yaml

    config = _seed(
        hermes_home,
        'mcp_servers:\n  "repowise":\n    command: old\n    env:\n      K: v\nmodel: a\n',
    )
    target = get_target("hermes")

    target.install(Scope.USER)
    text = config.read_text(encoding="utf-8")
    assert text.count("repowise:") == 1
    entry = yaml.safe_load(text)["mcp_servers"]["repowise"]
    assert entry["command"] != "old"
    assert entry["env"] == {"K": "v"}

    assert [written.action for written in target.install(Scope.USER).files] == [
        FileAction.UNCHANGED
    ]
    assert [written.action for written in target.uninstall(Scope.USER).files] == [
        FileAction.REMOVED
    ]
    assert "repowise" not in config.read_text(encoding="utf-8")


def test_yaml_merge_removes_a_child_from_an_inline_parent() -> None:
    """The mirror of the inline write, and it has to exist for the same reason.

    Teaching the write path about inline parents and leaving removal behind
    meant the child search looked for a line that is not on a line of its own,
    found nothing, reported the parent unchanged, and uninstall then failed its
    own consistency check and left the entry in place. An emptied inline parent
    comes back as ``{}``, which is what it was before the install.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    text, section = yaml_merge.remove_child(
        "mcp_servers: {github: {command: gh}, repowise: {command: x}}\n",
        "mcp_servers",
        "repowise",
    )
    assert text == "mcp_servers: {github: {command: gh}}\n"
    assert section == {"github": {"command": "gh"}}

    emptied, section = yaml_merge.remove_child(
        "mcp_servers: {repowise: {command: x}}\n", "mcp_servers", "repowise"
    )
    assert emptied == "mcp_servers: {}\n"
    assert section == {}


def test_yaml_merge_leaves_a_deeper_comment_with_the_child_it_documents() -> None:
    """A comment indented past the children belongs to the one above it.

    Stepping over it moves the user's note inside our block, where it reads as
    documenting our entry. The same indent rule keeps a ``#`` line that is
    *content* rather than a comment, a literal inside a ``key: |`` block
    scalar, from being stepped over at all, which truncated the scalar.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    kept = yaml_merge.set_child(
        "mcp_servers:\n  github:\n    command: gh\n    # note about github\nmodel: a\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )
    lines = kept.splitlines()
    assert lines.index("    # note about github") < lines.index("  repowise:")

    scalar = yaml_merge.set_child(
        "mcp_servers:\n  desc: |\n    # literal text\nmodel: a\n",
        "mcp_servers",
        "repowise",
        {"command": "x"},
    )
    assert yaml_merge.load_mapping(scalar)["mcp_servers"]["desc"] == "# literal text\n"


def test_yaml_merge_appends_above_a_comment_introducing_the_next_section() -> None:
    """The parent's range runs to the next top-level key, heading included.

    Stopping at the first blank line puts our entry below the user's section
    heading, which indents their title into our block and leaves the section it
    introduced without it. The document is unchanged either way, so ``verify``
    is structurally blind to this one.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    merged = yaml_merge.set_child(
        "mcp_servers:\n  foo:\n    command: x\n\n# ---- model settings ----\nmodel: a\n",
        "mcp_servers",
        "repowise",
        {"command": "y"},
    )

    lines = merged.splitlines()
    assert lines.index("  repowise:") < lines.index("# ---- model settings ----")
    assert lines.index("# ---- model settings ----") == lines.index("model: a") - 1


def test_yaml_merge_keeps_a_comment_on_the_line_it_replaces() -> None:
    """Replacing a child replaces its whole first line, comment included.

    Recovering it is conservative: a ``#`` inside a quoted scalar is not a
    comment, and moving a fragment of the user's data into one is a worse
    outcome than losing a comment in a rare shape.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    kept = yaml_merge.set_child(
        "platform_toolsets:\n  cli: [hermes-cli]   # deliberately an allowlist\n",
        "platform_toolsets",
        "cli",
        ["hermes-cli", "repowise"],
    )
    assert "# deliberately an allowlist" in kept
    assert yaml_merge.load_mapping(kept) == {
        "platform_toolsets": {"cli": ["hermes-cli", "repowise"]}
    }

    quoted = yaml_merge.set_child(
        'platform_toolsets:\n  cli: ["a#b"]\n', "platform_toolsets", "cli", ["x"]
    )
    assert "#" not in quoted
    assert yaml_merge.load_mapping(quoted) == {"platform_toolsets": {"cli": ["x"]}}


def test_yaml_merge_keeps_an_inline_list_inline_and_still_findable() -> None:
    """Editing one item of a user's inline list gives back an inline list.

    The second assertion is the one that matters, and it is the defect this
    pins: rendering the *pair* in flow style produces ``{cli: [a, b]}``, which
    is valid YAML and the right document, so nothing downstream complains --
    but the key is then inside a flow mapping where the line-based search
    cannot find it, and the next edit appends a second copy rather than
    replacing it. Uninstall is where that surfaces, as a removal that silently
    does nothing.
    """
    from repowise.cli.agent_targets.formats import yaml_merge

    text = "platform_toolsets:\n  cli: [hermes-cli, othersrv]\n"
    added = yaml_merge.set_child(
        text, "platform_toolsets", "cli", ["hermes-cli", "othersrv", "repowise"]
    )
    assert "  cli: [hermes-cli, othersrv, repowise]\n" in added
    assert "{cli:" not in added

    # The round trip has to land back on the exact bytes, which only holds if
    # the edited line is still reachable.
    restored = yaml_merge.set_child(
        added, "platform_toolsets", "cli", ["hermes-cli", "othersrv"]
    )
    assert restored == text
