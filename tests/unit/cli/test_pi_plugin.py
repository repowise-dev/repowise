"""Pi extension and adapter agreement, pinned to each other.

The extension is a TypeScript file Pi loads, and the adapter is the Python
module that answers it — two hand-maintained copies of the same contract
(tool names, helper commands, response shape, Lens toggles). A rename on
one side that the other does not follow is silent in exactly the way a
working hook is, so the shared literals are asserted here rather than
merely documented.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.cli.agent_adapters.pi import (
    EDIT_TOOL_NAMES,
    READ_TOOL_NAMES,
    SEARCH_TOOL_NAMES,
    SHELL_TOOL_NAMES,
)

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "pi"
EXTENSION = (PLUGIN_ROOT / "repowise.ts").read_text(encoding="utf-8")
README = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

LENS_KEYS = (
    "lens_margin",
    "lens_squeeze",
    "lens_review",
    "lens_flow",
    "lens_pane_autoopen",
    "lens_map_health",
)


def test_extension_routes_every_adapter_tool_name() -> None:
    """Every name the adapter routes on, the extension actually sends."""
    for name in SHELL_TOOL_NAMES | READ_TOOL_NAMES | EDIT_TOOL_NAMES | SEARCH_TOOL_NAMES:
        assert f'"{name}"' in EXTENSION, name
    # `ls` is in no adapter set and must stay unrouted: the extension
    # must not spend a subprocess on a listing nothing can enrich.
    assert '"ls"' not in EXTENSION


def test_extension_calls_the_helpers_with_the_pi_markers() -> None:
    assert '"--client", "pi"' in EXTENSION
    assert '"--agent", "pi"' in EXTENSION
    assert "repowise-augment" in EXTENSION
    assert "repowise-rewrite" in EXTENSION


def test_extension_subscribes_to_the_mapped_events() -> None:
    for event in ("SessionStart", "PostToolUse", "PostToolUseFailure", "PreToolUse"):
        assert f'"{event}"' in EXTENSION, event
    for pi_event in ("session_start", "before_agent_start", "tool_call", "tool_result"):
        assert f'"{pi_event}"' in EXTENSION, pi_event


def test_extension_reads_the_shared_response_shape() -> None:
    assert "hookSpecificOutput" in EXTENSION
    assert "additionalContext" in EXTENSION
    assert "updatedToolOutput" in EXTENSION
    assert "updatedInput" in EXTENSION


def test_lens_toggles_match_the_claude_plugin_defaults() -> None:
    """Same seven keys, same defaults, on both sides of the port.

    The Pi extension only *reads* these today (Lens is Claude Code-only),
    so a drifted default would be a lie the user configures against.
    """
    plugin = json.loads((ROOT / "plugins" / "claude-code" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    user_config = plugin["userConfig"]
    assert set(user_config) == set(LENS_KEYS)
    for key in LENS_KEYS:
        assert f"{key}:" in EXTENSION or f'"{key}"' in EXTENSION, key
        assert user_config[key]["type"] == "boolean"
        assert f"{key}: {str(user_config[key]['default']).lower()}" in EXTENSION, key


def test_package_manifest_declares_the_extension() -> None:
    manifest = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
    assert manifest["pi"]["extensions"] == ["./repowise.ts"]


def test_package_manifest_declares_pi_package_discovery() -> None:
    """Gallery keyword plus skills/prompts, per Pi's package guide (#5307).

    Conventional directories are discovered without a manifest, so the
    directories must exist as the generator's targets — not just be
    named in the manifest.
    """
    manifest = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
    assert manifest["keywords"] == ["pi-package"]
    assert manifest["pi"]["skills"] == ["./skills"]
    assert manifest["pi"]["prompts"] == ["./prompts/*.md"]
    assert (PLUGIN_ROOT / "skills").is_dir()
    assert (PLUGIN_ROOT / "prompts").is_dir()


def test_replacement_keeps_additional_context() -> None:
    """`updatedToolOutput` replaces the result but keeps the context note.

    The context block is kept ahead of the replacement — the two fields
    are not alternatives (a stale-read flag alongside a served skeleton).
    Only the note is kept: the original content stays dropped, or a
    skeleton/digest replacement would ship the full output it exists to
    condense (review finding, #5315).
    """
    assert "...(contextBlock ? [contextBlock] : [])," in EXTENSION
    assert '{ type: "text", text: replacement } satisfies TextBlock,' in EXTENSION
    # The old return dropped the note along with the original content.
    assert 'return { content: [{ type: "text", text: replacement }' not in EXTENSION


def test_empty_context_answer_marks_the_session() -> None:
    """An answered helper marks the session even when it says nothing.

    The session gate must be recorded as soon as the helper answers,
    and a failed helper must not be recorded — otherwise the first case
    re-spawns the helper every turn and the second suppresses context
    for the rest of the session (review finding, #5315).
    """
    assert "if (!result.answered) return;" in EXTENSION
    assert "injectedSessionId = sessionId;" in EXTENSION
    # The assignment must come before the empty-context early return.
    assert EXTENSION.index("injectedSessionId = sessionId;") < EXTENSION.index(
        'if (typeof context !== "string" || !context) return;'
    )


def test_missing_cwd_does_not_disable_the_helper() -> None:
    """ENOENT from a missing cwd must not mark the CLI as missing.

    Node raises ENOENT for a nonexistent cwd as well as a missing
    binary, so the `missing` set is touched only when the directory
    actually exists (review finding, #5315).
    """
    assert 'if (error.code === "ENOENT" && existsSync(cwd)) missing.add(cli);' in EXTENSION


def test_lens_limitation_is_documented_not_hidden() -> None:
    assert "Claude Code-only" in README
    assert "Claude Code-only" in EXTENSION
    # The one deliberate deviation from the issue's event table, stated
    # where the next reader will look for it.
    assert "agent_start" in EXTENSION
    assert "cannot inject" in EXTENSION
    assert "before_agent_start" in EXTENSION
