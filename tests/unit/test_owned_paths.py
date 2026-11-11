"""The repowise-owned path list, kept in step with the writers it describes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pathspec
import pytest

from repowise.core.owned_paths import MANAGED_FILES, OWNED_PATTERNS, only_repowise_differs


def _covered(relative: str) -> bool:
    owned = pathspec.PathSpec.from_lines("gitwildmatch", OWNED_PATTERNS)
    return relative in MANAGED_FILES or owned.match_file(relative)


def test_every_project_file_a_writer_names_is_listed(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.registry import all_targets
    from repowise.cli.agent_targets.types import Scope
    from repowise.cli.uninstall.generated_files import generated_blocks

    named = [block.relative_path for block in generated_blocks()]
    for target in all_targets():
        if target.supports_scope(Scope.PROJECT):
            for raw in target.describe_paths(Scope.PROJECT, repo_path=tmp_path):
                named.append(Path(raw).relative_to(tmp_path).as_posix())
    assert named
    assert [path for path in named if not _covered(path)] == []


_TAGS = ("REPOWISE", "REPOWISE_AGENTS")


def test_every_writers_marker_is_stripped() -> None:
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets.copilot import OWNER_END, OWNER_START
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator as Gen

    pairs = [
        (DISTILL_MARKER_START, DISTILL_MARKER_END),
        (OWNER_START, OWNER_END),
        *((Gen.MARKER_START_FMT.format(tag=t), Gen.MARKER_END_FMT.format(tag=t)) for t in _TAGS),
    ]
    for start, end in pairs:
        assert only_repowise_differs("AGENTS.md", "# Mine\n", f"# Mine\n\n{start}\nbody\n{end}\n")


def test_a_file_a_writer_creates_is_repowise_s_alone() -> None:
    """New file: the writer's placeholder sits above its block, and both are ours."""
    from repowise.cli.agent_targets.instructions import DISTILL_MARKER_END, DISTILL_MARKER_START
    from repowise.cli.agent_targets.targets.cursor import RULES_PREFIX
    from repowise.cli.editor_integrations.codex_config import _NEW_FILE_PLACEHOLDER
    from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
    from repowise.core.generation.editor_files.claude_md import (
        ClaudeMdGenerator,
        WorkspaceClaudeMdGenerator,
    )

    block = f"{DISTILL_MARKER_START}\nbody\n{DISTILL_MARKER_END}"
    for prefix, path in (
        (ClaudeMdGenerator.user_placeholder, ".claude/CLAUDE.md"),
        (WorkspaceClaudeMdGenerator.user_placeholder, ".claude/CLAUDE.md"),
        (AgentsMdGenerator.user_placeholder, "AGENTS.md"),
        (_NEW_FILE_PLACEHOLDER, "AGENTS.md"),
        (RULES_PREFIX, ".cursor/rules/repowise.mdc"),
    ):
        created = f"{prefix}\n{block}\n"
        assert only_repowise_differs(path, "", created)
        assert not only_repowise_differs(path, "", created + "\nMy rule.\n")


def _settings(allow: list[str], groups: list[dict]) -> str:
    hooks = {"hooks": {"PostToolUse": groups}} if groups else {}
    return json.dumps({"permissions": {"allow": allow}, **hooks})


def test_hook_and_permission_edits_are_matched_exactly() -> None:
    """Only what the writers emit is repowise's; text that merely names repowise is not."""
    from repowise.cli.editor_integrations.claude_config import _coverage_entry

    ours = _coverage_entry()
    mine = {"type": "command", "command": "make lint"}
    path = ".claude/settings.local.json"
    allow = ["Bash(cd /x/repowise && ls)"]
    # The coverage entry, added whole: repowise's.
    assert only_repowise_differs(path, _settings(allow, []), _settings(allow, [ours]))
    # A user removing a permission that names repowise: theirs.
    assert not only_repowise_differs(path, _settings(allow, [ours]), _settings([], [ours]))
    # A user's hook in a group repowise's hook also sits in: pruned per hook.
    mixed = {**ours, "hooks": [*ours["hooks"], mine]}
    edited = {**ours, "hooks": [*ours["hooks"], {**mine, "command": "make test"}]}
    assert not only_repowise_differs(path, _settings([], [mixed]), _settings([], [edited]))
    assert only_repowise_differs(
        path, _settings([], [{**ours, "hooks": [mine]}]), _settings([], [mixed])
    )
    # The Codex hooks file as its writer creates it.
    from repowise.cli.agent_targets.targets.codex import hooks_config

    assert only_repowise_differs(".codex/hooks.json", "", json.dumps(hooks_config()))
    # A hook that only resembles ours is the user's.
    lookalike = {**ours, "hooks": [{"type": "command", "command": "repowise hook x"}]}
    assert not only_repowise_differs(path, _settings([], []), _settings([], [lookalike]))


def _generator_pair(tag: str) -> tuple[str, str]:
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator as Gen

    return Gen.MARKER_START_FMT.format(tag=tag), Gen.MARKER_END_FMT.format(tag=tag)


_START, _END = _generator_pair("REPOWISE")
_USER_START, _USER_END = "<!-- REPOWISE_USER:START -->", "<!-- REPOWISE_USER:END -->"


@pytest.mark.parametrize(
    ("path", "old", "new", "ours"),
    [
        # A block refreshed in place, a block appended, and a user's edit beside it.
        ("AGENTS.md", f"x\n{_START}\nv1\n{_END}\n", f"x\n{_START}\nv2\n{_END}\n", True),
        ("AGENTS.md", "x\n", f"x\n\n{_START}\nv\n{_END}\n", True),
        ("AGENTS.md", "x\n", f"y\n{_START}\nv\n{_END}\n", False),
        # A block fenced by markers no writer emits is the user's, whatever its name.
        (
            "AGENTS.md",
            f"x\n{_USER_START}\nv1\n{_USER_END}\n",
            f"x\n{_USER_START}\nv2\n{_USER_END}\n",
            False,
        ),
        ("AGENTS.md", "", "<!-- REPOWISE:START -->\nv\n<!-- REPOWISE:END -->\n", False),
        # A user's own comment or a bare title is content, not a placeholder.
        ("AGENTS.md", "x\n", "x\n<!-- my note -->\n", False),
        (".claude/CLAUDE.md", "", "# CLAUDE.md\n", False),
        (".mcp.json", "", '{"mcpServers": {"repowise": {"command": "repowise"}}}', True),
        # A server the user added, even an empty one, counts.
        (".mcp.json", "", '{"mcpServers": {"repowise": {}, "other": {}}}', False),
        (".mcp.json", "", '{"mcpServers": {"repowise": {}, "other": {"command": "x"}}}', False),
        (".vscode/extensions.json", "", '{"recommendations": ["repowise-dev.repowise"]}', True),
        (".vscode/extensions.json", "", '{"recommendations": ["repowise-dev.other"]}', False),
        (".codex/config.toml", "", '[mcp_servers.repowise]\ncommand = "repowise"\n', True),
        (".codex/config.toml", "", 'model = "o3"\n', False),
        # A config with comments cannot be compared, so it is never repowise's alone.
        (".vscode/mcp.json", "", '// mine\n{"servers": {"repowise": {}}}', False),
    ],
)
def test_only_repowise_differs(path: str, old: str, new: str, ours: bool) -> None:
    assert only_repowise_differs(path, old, new) is ours


def test_a_renamed_managed_file_is_read_as_its_two_paths(tmp_path: Path) -> None:
    from repowise.core.analysis.git_cli import generated_worktree_paths

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "opencode.json").write_text('{"mcp": {"repowise": {}}}', encoding="utf-8")
    git("add", "opencode.json")
    git("-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-qm", "seed")
    git("mv", "opencode.json", "opencode.jsonc")

    assert generated_worktree_paths(str(tmp_path)) == ("/opencode.json", "/opencode.jsonc")
