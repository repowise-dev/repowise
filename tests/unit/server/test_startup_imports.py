"""Starting ``repowise serve`` or ``repowise mcp`` must not import an LLM SDK.

The openai SDK alone took 3 s of the ~8 s it took to import the web app,
pulled in only to read one string constant while listing models. Each SDK
is imported by the provider that uses it, when that provider is built.

The web app also leaves the MCP tool surface (every tool module and the mcp
SDK) and networkx to the first request that needs them.

Each probe runs in a fresh interpreter, so a module another test already
imported cannot hide a regression.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_LLM_SDKS = ("openai", "anthropic", "google.genai", "litellm")


def _probe(code: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    marker = [line for line in result.stdout.splitlines() if line.startswith("RESULT=")]
    assert marker, result.stdout
    return marker[-1].removeprefix("RESULT=")


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


def test_serve_import_leaves_the_tool_surface_and_networkx_for_first_use() -> None:
    loaded = _probe(
        "import sys, repowise.server.app; "
        "from repowise.server import mcp_server; "
        "heavy = [m for m in ('mcp', 'networkx') if m in sys.modules]; "
        "heavy += ['surface'] if mcp_server._surface_complete else []; "
        "print('RESULT=' + ','.join(heavy))"
    )
    assert loaded == "", f"importing the web app loaded: {loaded}"


def test_first_tool_surface_request_loads_the_surface() -> None:
    # A fresh interpreter, so this request really is the first to read it.
    out = _probe(
        "from fastapi.testclient import TestClient; "
        "from repowise.server.app import create_app; "
        "from repowise.server import mcp_server; "
        "response = TestClient(create_app()).get("
        "'/api/mcp/tools', headers={'Authorization': 'Bearer probe-key'}); "
        "names = {t['name'] for t in response.json()['tools']}; "
        "print('RESULT=%s %s %s' % (response.status_code, "
        "mcp_server._surface_complete, 'get_answer' in names))",
        env={"REPOWISE_API_KEY": "probe-key"},
    )
    assert out == "200 True True"
