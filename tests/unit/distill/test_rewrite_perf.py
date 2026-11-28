"""Hot-path guarantees for the ``repowise-rewrite`` PreToolUse hook.

The hook fires on EVERY Bash tool call an agent makes, so it must answer
quickly. Its median runtime is measured relative to interpreter startup on
the same runner, so runner speed scales both sides of the budget.
Two layers of protection:

  1. An import-graph guard: the hook module (and the adapters it uses) must
     never pull click, sqlalchemy, structlog, or any ``repowise.core``
     module — those are where the startup milliseconds hide.
  2. A calibrated wall-clock budget over repeated subprocess invocations,
     measured against the real console script when the venv provides one,
     plus a loose absolute ceiling on every invocation.
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

# Issue #2372 measured about 19 ms of interpreter startup plus 110 ms of hook
# overhead on WSL. The suggested 150/200 ms overhead allowances correspond
# to total-runtime ratios of about 9x/12x that baseline. Use those ratios so
# CPU contention scales the allowance too; a fixed additive allowance still
# failed under load at 267 ms for the hook versus 103 ms for the interpreter.
_PLAIN_RUNTIME_RATIO = 9
_LEDGERED_RUNTIME_RATIO = 12


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


def _median(cmd: list[str], payload: str) -> tuple[float, list[str], list[float]]:
    """Wall-clock timings over 12 invocations, plus every stdout seen.

    Returns ``(median_ms, outputs, all_timings_ms)``. The *median* is the
    quantity the budget is written about: a single slow subprocess spawn on a
    shared CI runner is a scheduling outlier, not a regression, and must not
    decide the run. The raw ``all_timings`` list is returned so the failure
    message can show the whole distribution — one outlier is then obvious at
    a glance instead of hiding behind a single number (issue #1783).
    """
    timings: list[float] = []
    outputs: list[str] = []
    for _ in range(12):
        start = time.perf_counter()
        result = subprocess.run(cmd, input=payload, capture_output=True, text=True)
        timings.append((time.perf_counter() - start) * 1000)
        assert result.returncode == 0
        outputs.append(result.stdout)
    timings.sort()
    median = timings[len(timings) // 2]
    return median, outputs, timings


def _assert_budget(cmd: list[str], payload: str, runtime_ratio: float) -> list[str]:
    """Calibrate each batch with the same interpreter, input and pipe setup."""
    baseline, _, baseline_timings = _median([sys.executable, "-c", "pass"], payload)
    median, outputs, timings = _median(cmd, payload)
    budget = baseline * runtime_ratio
    samples = ", ".join(f"{t:.0f}" for t in timings)
    baseline_samples = ", ".join(f"{t:.0f}" for t in baseline_timings)
    assert median < budget, (
        f"repowise-rewrite runtime {median:.1f} ms >= {runtime_ratio}x baseline "
        f"(median {median:.1f} ms, baseline {baseline:.1f} ms; "
        f"hook: {samples}; interpreter: {baseline_samples})"
    )
    # Still catch an invocation taking a second, even if the median or the
    # baseline hides it. One scheduling outlier below this ceiling is fine.
    assert max(timings) < 1000, f"repowise-rewrite max {max(timings):.0f} ms >= 1000 ms"
    return outputs


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


def test_rewrite_overhead_stays_under_budget(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    cmd = _hook_invocation()

    # Warmup: first run pays one-off filesystem cache costs.
    subprocess.run(cmd, input=_payload("pytest -x", tmp_path), capture_output=True, text=True)

    outputs = _assert_budget(cmd, _payload("pytest -x", tmp_path), _PLAIN_RUNTIME_RATIO)
    assert all("repowise distill --source hook-bash pytest -x" in out for out in outputs)


def test_a_ledgered_invocation_stays_under_budget(tmp_path: Path) -> None:
    """The path an agent in an indexed repo actually takes.

    Every other timing test here sends a payload with no ``session_id``, which
    skips the ledger write entirely — so they measure a path no real session
    uses and would not have noticed the write at all.
    """
    (tmp_path / ".repowise").mkdir()
    cmd = _hook_invocation()
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -x"},
            "cwd": str(tmp_path),
            "session_id": "perf",
        }
    )
    subprocess.run(cmd, input=payload, capture_output=True, text=True)

    outputs = _assert_budget(cmd, payload, _LEDGERED_RUNTIME_RATIO)
    assert all("repowise distill --source hook-bash pytest -x" in out for out in outputs)
    # Guard the guard: a budget met by not writing the row would pass forever.
    db = tmp_path / ".repowise" / "sessions" / "sessions.db"
    assert db.exists(), "the probe never reached the ledger write it is timing"


@pytest.mark.parametrize("command", _PERF_COMMANDS)
def test_lexer_shapes_stay_under_budget(tmp_path: Path, command: str) -> None:
    """No command shape may blow the budget, whatever the lexer walks.

    Only the timing is asserted here; whether a given shape rewrites is the
    decision table's business (and is platform-dependent for pipelines).
    """
    (tmp_path / ".repowise").mkdir()
    cmd = _hook_invocation()
    subprocess.run(cmd, input=_payload(command, tmp_path), capture_output=True, text=True)

    _assert_budget(cmd, _payload(command, tmp_path), _PLAIN_RUNTIME_RATIO)


@pytest.mark.parametrize(
    ("baseline_ms", "hook_ms", "runtime_ratio", "worst_ms", "failure"),
    [
        (19, 129, _PLAIN_RUNTIME_RATIO, 129, None),
        (19, 200, _LEDGERED_RUNTIME_RATIO, 200, None),
        (103, 267, _PLAIN_RUNTIME_RATIO, 306, None),
        (250, 360, _PLAIN_RUNTIME_RATIO, 360, None),
        (19, 180, _PLAIN_RUNTIME_RATIO, 180, "runtime"),
        (19, 230, _LEDGERED_RUNTIME_RATIO, 230, "runtime"),
        (250, 360, _PLAIN_RUNTIME_RATIO, 1000, "max"),
    ],
)
def test_calibration_preserves_regression_guards(
    monkeypatch: pytest.MonkeyPatch,
    baseline_ms: float,
    hook_ms: float,
    runtime_ratio: float,
    worst_ms: float,
    failure: str | None,
) -> None:
    """A slow runner is allowed; extra hook work and a one-second outlier are not."""
    measurements = iter([
        (baseline_ms, [""], [baseline_ms] * 12),
        (hook_ms, ["rewritten"] * 12, [hook_ms] * 11 + [worst_ms]),
    ])
    monkeypatch.setattr(sys.modules[__name__], "_median", lambda *_: next(measurements))
    if failure:
        with pytest.raises(AssertionError, match=failure):
            _assert_budget(["hook"], "payload", runtime_ratio)
    else:
        assert _assert_budget(["hook"], "payload", runtime_ratio) == ["rewritten"] * 12
