"""Hot-path guarantees for the ``repowise-rewrite`` PreToolUse hook.

The hook fires on EVERY Bash tool call an agent makes, so it must answer in
well under 100 ms p95. Two layers of protection:

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


def test_p95_under_100ms(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -x"},
            "cwd": str(tmp_path),
        }
    )
    cmd = _hook_invocation()

    # Warmup: first run pays one-off filesystem cache costs.
    subprocess.run(cmd, input=payload, capture_output=True, text=True)

    timings: list[float] = []
    for _ in range(12):
        start = time.perf_counter()
        result = subprocess.run(cmd, input=payload, capture_output=True, text=True)
        timings.append((time.perf_counter() - start) * 1000)
        assert result.returncode == 0
        assert "repowise distill pytest -x" in result.stdout

    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 < 100, f"repowise-rewrite p95 {p95:.1f} ms >= 100 ms (all: {timings})"
