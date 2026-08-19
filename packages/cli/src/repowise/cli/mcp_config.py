"""Generic MCP config helpers, plus the compatibility surface for the savers.

What lives here now is only what is genuinely agent-independent: how a
registration spells the repowise command, the standard ``mcpServers`` payload,
the ``.repowise/mcp.json`` write, and the Codex CLI process probes.

The per-editor ``save_*`` functions that used to hold 531 lines of flat
per-agent knowledge are now **delegators**. Their bodies live with the agent
they belong to, in ``repowise.cli.agent_targets.targets``, alongside that
agent's detection, uninstall and doctor logic. They are kept as names here for
one reason: they are a long-standing public surface with direct callers and
direct test coverage, and breaking that would be a second change riding along
with a rewrite whose whole value is that it changes nothing observable.

The property that matters is not that these names are gone — it is that a *new*
agent adds nothing to this module. Everything a fourth agent needs is a
descriptor file and a registry line.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _looks_transient(path: Path) -> bool:
    """True when *path* lives somewhere that won't survive (temp, uvx cache).

    ``uvx repowise`` runs from an ephemeral cache environment; pinning a
    registration to it would break on the next cache eviction. Same for
    anything under the OS temp dir.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return True
    try:
        if resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            return True
    except (OSError, ValueError):
        pass
    parts = {part.lower() for part in resolved.parts}
    # uv tool-run cache: ~/.cache/uv/archive-v0/... (POSIX) or
    # %LOCALAPPDATA%/uv/cache/archive-v0/... (Windows).
    return "uv" in parts and ("cache" in parts or ".cache" in parts)


def resolve_repowise_command(script: str = "repowise") -> str:
    """Absolute path of the *running* install's console script, or the bare name.

    Registrations that store the bare command name are resolved via PATH at
    session start, so any shadow install (conda, old pip, pipx, uv tool)
    silently hijacks the MCP server. For **per-user** config files we pin
    the absolute path of the install that ran ``init`` instead. Repo-shared
    files (``.mcp.json``, ``.codex/config.toml``) must keep the bare name —
    they may be committed, and one contributor's absolute path would break
    everyone else's checkout.

    The lookup is the running interpreter's scripts directory (``Scripts``
    on Windows, ``bin`` elsewhere) — i.e. the venv/conda/pipx/uv-tool
    environment actually executing right now, never PATH. Falls back to the
    bare name when the script isn't there (e.g. ``python -m`` from a source
    checkout) or the location is transient (uvx cache, temp dir).
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    try:
        candidate = Path(sys.executable).parent / f"{script}{suffix}"
        if candidate.is_file() and not _looks_transient(candidate):
            return str(candidate.resolve()).replace("\\", "/")
    except OSError:
        pass
    return script


def generate_mcp_config(repo_path: Path, *, command: str | None = None) -> dict:
    """Generate MCP config JSON for a repository.

    Returns a dict in the standard mcpServers format. *command* defaults to
    the bare ``repowise`` (PATH-resolved) — callers writing **per-user**
    config files should pass ``resolve_repowise_command()`` to pin the
    registration to the install that ran ``init``; repo-shared files keep
    the default (see :func:`resolve_repowise_command`).
    """
    abs_path = str(repo_path.resolve()).replace("\\", "/")
    return {
        "mcpServers": {
            "repowise": {
                "command": command or "repowise",
                "args": ["mcp", abs_path, "--transport", "stdio"],
                "description": "repowise: codebase intelligence — docs, graph, git signals, dead code, decisions",
            }
        }
    }


# ---------------------------------------------------------------------------
# Codex CLI process probes
#
# Not writers, so they stay here: they answer "is this agent usable on this
# machine" for the UI and for init's prompts, and both callers want them
# without importing the target.
# ---------------------------------------------------------------------------


def resolve_codex_executable() -> str | None:
    """Return the executable path used to launch Codex, or None if unavailable."""

    return shutil.which("codex")


def is_codex_cli_installed() -> bool:
    """Return True when the Codex CLI is on PATH and runnable."""

    codex_cmd = resolve_codex_executable()
    if not codex_cmd:
        return False
    try:
        result = subprocess.run(
            [codex_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def is_codex_logged_in() -> bool:
    """Return True when the local Codex CLI reports an authenticated session."""

    codex_cmd = resolve_codex_executable()
    if not codex_cmd or not is_codex_cli_installed():
        return False
    try:
        result = subprocess.run(
            [codex_cmd, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Generic writes
# ---------------------------------------------------------------------------


def save_mcp_config(repo_path: Path) -> Path:
    """Save MCP config to .repowise/mcp.json and return the path.

    Agent-independent: this is repowise's own copy of the payload, not any
    particular host's config, so it stays here rather than moving to a target.
    """
    from repowise.cli.agent_targets.formats.json_merge import write_json_config

    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    config_path = repowise_dir / "mcp.json"
    write_json_config(config_path, generate_mcp_config(repo_path))
    return config_path


def merge_mcp_entry(config_path: Path, new_entry: dict) -> bool:
    """Merge *new_entry* into the mcpServers block of *config_path*.

    Creates the file if it doesn't exist. Returns True on success.

    The per-server merge is deep: generated fields overwrite stored ones, but
    user-added keys such as an ``env`` block are preserved across
    re-registration (see ``formats.json_merge.merge_server_entries``).
    """
    from repowise.cli.agent_targets.formats.json_merge import (
        merge_server_entries,
        write_json_config,
    )

    try:
        if config_path.exists():
            existing = load_existing_config(config_path)
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        servers = dict(existing.get("mcpServers", {}))
        merge_server_entries(servers, new_entry)
        existing["mcpServers"] = servers
        write_json_config(config_path, existing)
        return True
    except OSError:
        return False


def load_existing_config(config_path: Path) -> dict:
    """Load an existing JSON config without silently replacing bad content."""
    from repowise.cli.agent_targets.formats.json_merge import load_json_object

    return load_json_object(config_path)


# ---------------------------------------------------------------------------
# Per-agent delegators
#
# Each forwards to the target that owns the format. See the module docstring
# for why the names survive the move.
# ---------------------------------------------------------------------------


def generate_codex_mcp_server_config(repo_path: Path) -> dict[str, object]:
    """Generate the Codex config.toml server table for repowise."""
    from repowise.cli.agent_targets.targets import codex

    return codex.server_table(repo_path)


def generate_codex_hooks_config() -> dict[str, object]:
    """Generate project-local Codex hooks for repowise context and freshness checks."""
    from repowise.cli.agent_targets.targets import codex

    return codex.hooks_config()


def enable_codex_hooks_feature(repo_path: Path) -> Path:
    """Enable Codex hooks in project-local .codex/config.toml."""
    from repowise.cli.agent_targets.targets import codex

    return codex.enable_hooks_feature(repo_path).path


def save_codex_mcp_config(repo_path: Path) -> Path:
    """Merge the repowise MCP server into project-local .codex/config.toml."""
    from repowise.cli.agent_targets.targets import codex

    return codex.write_server_config(repo_path).path


def save_codex_hooks_config(repo_path: Path) -> Path:
    """Merge repowise hooks into project-local .codex/hooks.json."""
    from repowise.cli.agent_targets.targets import codex

    return codex.write_hooks_config(repo_path)[0].path


def save_root_mcp_config(repo_path: Path) -> Path:
    """Write .mcp.json at repo root for MCP clients that support discovery."""
    from repowise.cli.agent_targets.targets import claude_code

    return claude_code.write_project_mcp_config(repo_path).path


def generate_vscode_mcp_server_entry(repo_path: Path) -> dict:
    """Generate the VS Code ``.vscode/mcp.json`` server entry for repowise."""
    from repowise.cli.agent_targets.targets import vscode

    return vscode.server_entry(repo_path)


def save_vscode_mcp_config(repo_path: Path) -> Path:
    """Merge the repowise server into ``.vscode/mcp.json`` and return the path.

    Raises ``ValueError`` when an existing file is not strict JSON (VS Code
    allows JSONC comments) or is not shaped as expected, so callers can skip the
    merge without destroying the file.
    """
    from repowise.cli.agent_targets.targets import vscode

    return vscode.write_mcp_config(repo_path).path


def save_vscode_extensions_config(repo_path: Path) -> Path:
    """Recommend the repowise extension in ``.vscode/extensions.json``.

    Raises ``ValueError`` on a file that is not strict JSON, for the same reason
    as :func:`save_vscode_mcp_config`.
    """
    from repowise.cli.agent_targets.targets import vscode

    return vscode.write_extensions_config(repo_path).path


#: Re-exported for the Codex hooks writer's own use and for callers that used
#: to reach it through this module.
def _is_repowise_hook(hook: dict) -> bool:
    from repowise.cli.agent_targets.targets.codex import _is_augment_hook

    return _is_augment_hook(hook)


def _has_repowise_hook_for_matcher(hook_list: list, matcher: object) -> bool:
    """Check if a repowise augment hook is registered for a matcher group."""
    from repowise.cli.agent_targets.targets.codex import _has_augment_hook_for_matcher

    return _has_augment_hook_for_matcher(hook_list, matcher)


__all__ = [
    "enable_codex_hooks_feature",
    "generate_codex_hooks_config",
    "generate_codex_mcp_server_config",
    "generate_mcp_config",
    "generate_vscode_mcp_server_entry",
    "is_codex_cli_installed",
    "is_codex_logged_in",
    "load_existing_config",
    "merge_mcp_entry",
    "resolve_codex_executable",
    "resolve_repowise_command",
    "save_codex_hooks_config",
    "save_codex_mcp_config",
    "save_mcp_config",
    "save_root_mcp_config",
    "save_vscode_extensions_config",
    "save_vscode_mcp_config",
]
