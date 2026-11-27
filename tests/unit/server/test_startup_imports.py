"""Starting ``repowise serve`` or ``repowise mcp`` must not import an LLM SDK.

The openai SDK alone took 3 s of the ~8 s it took to import the web app,
pulled in only to read one string constant while listing models. Each SDK
is imported by the provider that uses it, when that provider is built.

Each probe runs in a fresh interpreter, so a module another test already
imported cannot hide a regression.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_LLM_SDKS = ("openai", "anthropic", "google.genai", "litellm")


@pytest.mark.parametrize(
    "module",
    ["repowise.server.app", "repowise.server.mcp_server._server"],
)
def test_startup_import_loads_no_llm_sdk(module: str) -> None:
    probe = (
        f"import sys, {module}; "
        f"print('LOADED=' + ','.join(m for m in {_LLM_SDKS!r} if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    marker = [line for line in result.stdout.splitlines() if line.startswith("LOADED=")]
    assert marker, result.stdout
    loaded = marker[-1].removeprefix("LOADED=")
    assert loaded == "", f"importing {module} loaded LLM SDKs: {loaded}"
