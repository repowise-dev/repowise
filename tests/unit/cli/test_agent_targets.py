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
    assert ALL_IDS == ["claude-code", "codex", "vscode", "cursor", "opencode"]


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
    # Repo-shared file keeps the bare command; the per-user one pins its install.
    assert project["command"][0] == "repowise"


def test_opencode_round_trips_to_nothing(tmp_path: Path) -> None:
    """Install then uninstall leaves no file and no wrapper key behind.

    Including the ``$schema`` this target seeds into a file it created: a
    leftover ``{"$schema": ...}`` is a file the user never asked for and still
    reads as repowise having been here.
    """
    opencode = _opencode()
    repo = tmp_path / "repo"
    repo.mkdir()

    opencode.TARGET.install(Scope.PROJECT, repo_path=repo)
    config = opencode.config_path(Scope.PROJECT, repo)
    assert config.name == "opencode.jsonc"
    assert json.loads(config.read_text(encoding="utf-8"))["$schema"]

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


def test_opencode_keeps_a_config_holding_more_than_our_own_schema(tmp_path: Path) -> None:
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
