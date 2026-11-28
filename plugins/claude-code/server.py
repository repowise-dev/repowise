"""Start the repowise MCP server for this plugin.

Claude Code runs this file from ``.mcp.json`` as
``uv run --project ${CLAUDE_PLUGIN_ROOT} --frozen ... server.py``. It uses only
the standard library. The server is the ``repowise`` command from the user's
own install, started as ``repowise mcp`` on the same stdin and stdout: the
same repowise the user runs in a terminal, whose version the plugin leaves alone.

An API key saved in the plugin's settings reaches that one process under its
usual variable name. A key left empty changes nothing: the server keeps what
the shell or ``.repowise/.env`` already sets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

#: ``.mcp.json`` passes each saved key under the name on the left; repowise
#: reads it under the name on the right.
PLUGIN_KEYS = {
    "REPOWISE_PLUGIN_GEMINI_API_KEY": "GEMINI_API_KEY",
    "REPOWISE_PLUGIN_OPENAI_API_KEY": "OPENAI_API_KEY",
    "REPOWISE_PLUGIN_ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
}


def server_env(env: dict[str, str]) -> dict[str, str]:
    """*env* with each saved key under repowise's name, and the plugin's names removed."""
    out = {k: v for k, v in env.items() if k not in PLUGIN_KEYS}
    for plugin_name, name in PLUGIN_KEYS.items():
        value = env.get(plugin_name, "").strip()
        # Empty when the option is unset; an unexpanded "${user_config.*}" is unset too.
        if value and not value.startswith("${"):
            out[name] = value
    return out


def find_repowise() -> str | None:
    """The ``repowise`` a bare command name starts, as Claude Code resolved it before.

    On Windows that is ``repowise.exe``; ``shutil.which`` alone could pick a
    ``.cmd`` or ``.bat`` shim from an earlier PATH folder first.
    """
    if os.name == "nt":
        return shutil.which("repowise.exe") or shutil.which("repowise")
    return shutil.which("repowise")


def main() -> int:
    exe = find_repowise()
    if exe is None:
        sys.stderr.write(
            "repowise is not installed or not on PATH. Install it with "
            "`uv tool install repowise` (or pipx, or pip), then restart Claude Code.\n"
        )
        return 1
    env = server_env(dict(os.environ))
    if os.name == "nt":
        # os.execv on Windows starts a new process and exits this one, which
        # Claude Code would read as the server exiting: wait on it instead.
        return subprocess.call([exe, "mcp"], env=env)
    os.execve(exe, [exe, "mcp"], env)
    return 1  # not reached: execve replaces this process


if __name__ == "__main__":
    sys.exit(main())
