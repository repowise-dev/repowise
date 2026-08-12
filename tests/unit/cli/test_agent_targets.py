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


def test_registry_exposes_the_three_shipped_targets() -> None:
    assert ALL_IDS == ["claude-code", "codex", "vscode"]


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
        resolve_target_flag("cursor,codex", tmp_path)
    message = str(excinfo.value)
    assert "cursor" in message
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
