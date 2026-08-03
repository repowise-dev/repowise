"""Import-graph guarantees for the ``repowise-augment`` PostToolUse hook.

The hook fires on every Bash/Grep/Glob/Read/Edit/Write tool call an agent
makes, and ~98% of those fires emit nothing — so whatever it imports is
paid roughly 50-100 times a session for silence. Two module-level imports
have each cost ~800ms here already (``persistence.sql`` in
``augment_cmd.search``, ``core.workspace.config`` in ``claude_config``),
and neither was visible until someone ran ``-X importtime``. These guards
make the next one fail a test instead.

Structured like ``tests/unit/distill/test_rewrite_perf.py``, which does the
same job for the ``repowise-rewrite`` PreToolUse hook.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Import roots that mean the heavy stack got pulled in. ``repowise.cli.main``
#: is here because the hook has its own console script precisely so it never
#: pays the CLI's command surface.
_HEAVY_PREFIXES = (
    "networkx",
    "scipy",
    "sqlalchemy",
    "repowise.core.workspace",
    "repowise.core.ingestion",
    "repowise.core.pipeline",
    "repowise.core.persistence",
    "repowise.cli.main",
)


def _fake_home(tmp_path: Path) -> dict[str, str]:
    """A throwaway HOME so the self-heal cannot touch the real settings.json.

    ``migrate_claude_code_hooks`` *writes* ``~/.claude/settings.json`` when it
    finds anything to migrate, and ``Path.home()`` reads ``USERPROFILE`` on
    Windows and ``HOME`` elsewhere. Both are redirected. What is under test is
    the import graph, which is identical either way.
    """
    env = dict(os.environ)
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)
    return env


def _heavy_after(statements: str, env: dict[str, str] | None = None) -> str:
    code = (
        "import sys; "
        f"{statements} "
        f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def test_hook_entry_point_imports_nothing_heavy() -> None:
    heavy = _heavy_after("import repowise.cli.augment_hook;")
    assert heavy == "", f"the augment hook entry point pulled in:\n{heavy}"


def test_the_self_heal_imports_nothing_heavy(tmp_path: Path) -> None:
    """``migrate_claude_code_hooks`` runs after *every* hook invocation.

    It reads and rewrites ``~/.claude/settings.json`` — pure JSON work. A
    module-level ``core.workspace.config`` import for the unrelated MCP
    registration helpers made it cost 849ms of that, on every fire.
    """
    heavy = _heavy_after(
        "from repowise.cli.editor_integrations.claude_config import migrate_claude_code_hooks; "
        "migrate_claude_code_hooks();",
        env=_fake_home(tmp_path),
    )
    assert heavy == "", f"the hook self-heal pulled in:\n{heavy}"


def test_a_silent_invocation_imports_nothing_heavy(tmp_path: Path) -> None:
    """A payload the hook has nothing to say about must stay on the cheap path."""
    code = (
        "import sys, json, io; "
        "payload = json.dumps({'hook_event_name': 'PostToolUse', 'tool_name': 'Grep', "
        "'tool_input': {'pattern': 'zzz_no_such_symbol'}, 'tool_response': {'numFiles': 0}, "
        "'cwd': '', 'session_id': ''}); "
        "sys.stdin = io.StringIO(payload); "
        "from repowise.cli.commands.augment_cmd import _run_augment; "
        "_run_augment(client=None); "
        f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert out.stdout.strip() == "", f"a silent hook invocation pulled in:\n{out.stdout}"
