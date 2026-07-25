"""Hot-path guarantees for the ``repowise-rewrite`` PreToolUse hook.

The hook fires on EVERY Bash tool call an agent makes, so it must answer
quickly — a 150 ms p95 budget, which comfortably clears interpreter/console-
script startup even on slower Windows hosts while still catching a regression
that pulls in the heavy stack. Two layers of protection:

  1. An import-graph guard: the hook module (and the adapters it uses) must
     never pull click, sqlalchemy, structlog, or any ``repowise.core``
     module — those are where the startup milliseconds hide.
  2. An end-to-end wall-clock budget over repeated subprocess invocations,
     measured against the real console script when the venv provides one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HEAVY_PREFIXES = (
    "click",
    "sqlalchemy",
    "structlog",
    "networkx",
    "yaml",
    "rich",
    "repowise.core",
    "repowise.cli.main",
    "repowise.cli.helpers",
)


def test_import_pulls_no_heavy_modules() -> None:
    """Importing the hook module must not load the heavy stack.

    (yaml IS imported lazily when a config.yaml exists — that's a deliberate
    pay-only-when-needed cost — but plain import must stay clean.)
    """
    code = (
        "import sys; "
        "import repowise.cli.rewrite_hook; "
        "import repowise.cli.agent_adapters.claude_code; "
        f"heavy = [m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})]; "
        "print('\\n'.join(heavy)); "
        "sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"heavy imports leaked:\n{result.stdout}{result.stderr}"


def _hook_invocation() -> list[str]:
    """Prefer the real console script (what the agent actually runs)."""
    exe = Path(sys.executable).parent / (
        "repowise-rewrite.exe" if sys.platform == "win32" else "repowise-rewrite"
    )
    if exe.exists():
        return [str(exe)]
    return [sys.executable, "-c", "from repowise.cli.rewrite_hook import main; main()"]


def _payload(command: str, cwd: Path) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(cwd),
        }
    )


def _p95(cmd: list[str], payload: str) -> tuple[float, list[str]]:
    """Wall-clock p95 over 12 invocations, plus every stdout seen."""
    timings: list[float] = []
    outputs: list[str] = []
    for _ in range(12):
        start = time.perf_counter()
        result = subprocess.run(cmd, input=payload, capture_output=True, text=True)
        timings.append((time.perf_counter() - start) * 1000)
        assert result.returncode == 0
        outputs.append(result.stdout)
    timings.sort()
    return timings[int(len(timings) * 0.95) - 1], outputs


#: Command shapes the hook must answer within budget. The pipeline and
#: quoted-operator rows are the ones that exercise the shell lexer end to
#: end: they are the shapes that used to short-circuit on a character scan
#: and now walk every token.
_PERF_COMMANDS = (
    "pytest 2>&1 | grep FAIL",
    "git log --oneline -50 | rg fix",
    'git commit -m "fix a|b"',
    "npm run build --workspace packages/web -- --sourcemap --minify=false",
)


def test_p95_under_150ms(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    cmd = _hook_invocation()

    # Warmup: first run pays one-off filesystem cache costs.
    subprocess.run(cmd, input=_payload("pytest -x", tmp_path), capture_output=True, text=True)

    p95, outputs = _p95(cmd, _payload("pytest -x", tmp_path))
    assert all("repowise distill --source hook-bash pytest -x" in out for out in outputs)
    assert p95 < 150, f"repowise-rewrite p95 {p95:.1f} ms >= 150 ms"


@pytest.mark.parametrize("command", _PERF_COMMANDS)
def test_lexer_shapes_stay_under_budget(tmp_path: Path, command: str) -> None:
    """No command shape may blow the budget, whatever the lexer walks.

    Only the timing is asserted here; whether a given shape rewrites is the
    decision table's business (and is platform-dependent for pipelines).
    """
    (tmp_path / ".repowise").mkdir()
    cmd = _hook_invocation()
    subprocess.run(cmd, input=_payload(command, tmp_path), capture_output=True, text=True)

    p95, _ = _p95(cmd, _payload(command, tmp_path))
    assert p95 < 150, f"{command!r} p95 {p95:.1f} ms >= 150 ms"
