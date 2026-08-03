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

import json
import os
import sqlite3
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
    # 216ms, and it hid behind the others: the Read path reached it through
    # distill.store's module scope while every other guard here still passed.
    "structlog",
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


def _read_payload_probe(repo: Path, rel: str) -> str:
    """Source that fires the PostToolUse Read hook against a real file.

    ``tool_response`` is Read's real shape, ``content`` included: the
    replacement is built from this object, so a probe that stubs it loses the
    very path it claims to be timing.
    """
    source = (repo / rel).read_text(encoding="utf-8")
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(repo / rel)},
        "tool_response": {
            "type": "text",
            "file": {
                "filePath": str(repo / rel),
                "content": source,
                "numLines": len(source.splitlines()),
                "startLine": 1,
                "totalLines": len(source.splitlines()),
            },
        },
        "cwd": str(repo),
        "session_id": "perf",
    }
    return (
        "import sys, json, io; "
        f"sys.stdin = io.StringIO({json.dumps(payload)!r}); "
        "from repowise.cli.commands.augment_cmd import _run_augment; "
        "_run_augment(client=None); "
    )


def _indexed_repo(tmp_path: Path, *, opted_in: bool) -> tuple[Path, str]:
    """A repo the Read hook will take all the way to the skeleton path."""
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    rel = "big.py"
    lines = []
    for i in range(60):
        lines.append(f"def func_{i}(a, b):")
        lines.extend(f"    x{j} = a + b + {j}" for j in range(20))
        lines.append("")
    source = "\n".join(lines)
    (repo / rel).write_text(source, encoding="utf-8")

    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute(
        "CREATE TABLE wiki_symbols (file_path TEXT, name TEXT, kind TEXT, "
        "signature TEXT, start_line INTEGER, end_line INTEGER)"
    )
    for i in range(60):
        start = i * 22 + 1
        con.execute(
            "INSERT INTO wiki_symbols VALUES (?, ?, ?, ?, ?, ?)",
            (rel, f"func_{i}", "function", f"def func_{i}(a, b)", start, start + 20),
        )
    con.commit()
    con.close()

    if opted_in:
        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_skeleton: true\n", encoding="utf-8"
        )
    return repo, rel


def test_a_read_that_serves_a_skeleton_imports_nothing_heavy(tmp_path: Path) -> None:
    """The Read hook's most expensive path, guarded at its most expensive.

    ``repowise.core.distill.skeleton`` used to cost 556ms to import — not for
    anything in the skeleton, but because a package ``__init__`` runs on any
    submodule import and ``budget.py`` reached into ``generation.context`` for
    a four-line heuristic. Both are fixed; this is what keeps them fixed.
    """
    repo, rel = _indexed_repo(tmp_path, opted_in=True)
    env = _fake_home(tmp_path)
    env["REPOWISE_HOOK_UPDATED_OUTPUT"] = "1"
    code = (
        _read_payload_probe(repo, rel)
        + f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    # Guard the guard: a gate that silently stopped firing would pass an
    # import-graph assertion trivially, which is how this test would rot.
    assert "updatedToolOutput" in out.stdout, "the probe did not reach the skeleton path"
    assert out.stderr.strip() == "", f"a skeleton-serving Read pulled in:\n{out.stderr}"


def test_a_read_in_a_repo_that_did_not_opt_in_imports_nothing_heavy(tmp_path: Path) -> None:
    """Off by default has to be *cheap* by default, not merely quiet."""
    repo, rel = _indexed_repo(tmp_path, opted_in=False)
    code = (
        _read_payload_probe(repo, rel)
        + f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert "updatedToolOutput" not in out.stdout, "an opted-out repo had its Read replaced"
    assert out.stderr.strip() == "", f"an opted-out Read pulled in:\n{out.stderr}"


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
