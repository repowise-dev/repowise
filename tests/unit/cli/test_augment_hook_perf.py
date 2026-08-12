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


def test_the_stamped_self_heal_imports_nothing_heavy(tmp_path: Path) -> None:
    """The version stamp sits in front of the migrations, so it is hot-path too."""
    heavy = _heavy_after(
        "from repowise.cli.self_heal import run_editor_migrations; run_editor_migrations();",
        env=_fake_home(tmp_path),
    )
    assert heavy == "", f"the stamped self-heal pulled in:\n{heavy}"


def test_the_stamped_common_path_imports_nothing_at_all(tmp_path: Path) -> None:
    """Not "nothing heavy" but *nothing*, on the path that fires per tool call.

    The version of this that only checked the heavy-prefix list could not see the
    defect it should have caught. Gating the stamp read behind
    ``is_editor_setup_disabled`` reads better and costs more than the problem it
    solves: that lives in ``editor_setup``, whose module scope imports
    ``agent_targets.types``, and the pair measured at ~13ms against the ~19ms the
    whole self-heal was costing: two thirds of the saving spent on the check.
    Neither module is on the heavy list, and neither ever will be, because both
    are stdlib-only by design. Only naming them directly catches it.

    So: warm the stamp, then assert the second call reaches neither.
    """
    probe = (
        "import sys; "
        "from repowise.cli.self_heal import run_editor_migrations; "
        "run_editor_migrations(); "
        "[sys.modules.pop(m) for m in list(sys.modules) "
        " if m.startswith(('repowise.cli.editor_setup','repowise.cli.agent_targets',"
        "'repowise.cli.editor_integrations'))]; "
        "run_editor_migrations(); "
        "print('\\n'.join(sorted(m for m in sys.modules "
        " if m.startswith(('repowise.cli.editor_setup','repowise.cli.agent_targets',"
        "'repowise.cli.editor_integrations')))));"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert out.stdout.strip() == "", (
        "the stamped common path imported these, which the heavy-prefix guard "
        f"cannot see:\n{out.stdout}"
    )


def test_the_stamp_spares_the_settings_read_on_the_common_path(tmp_path: Path) -> None:
    """The point of the stamp, measured as file access rather than asserted.

    Before it, every hook fire opened and parsed ``~/.claude/settings.json`` to
    discover there was nothing to do, once per matched tool call. The second
    run must not open it at all.
    """
    env = _fake_home(tmp_path)
    probe = (
        "import json, pathlib; "
        "from repowise.cli.self_heal import run_editor_migrations, stamp_path; "
        "run_editor_migrations(); "
        "opened = []; "
        "import builtins; real = builtins.open; "
        "builtins.open = lambda f, *a, **k: (opened.append(str(f)), real(f, *a, **k))[1]; "
        "run_editor_migrations(); "
        "builtins.open = real; "
        "print('\\n'.join(p for p in opened if 'settings.json' in p or 'hooks.json' in p));"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip() == "", f"the stamped run still read:\n{out.stdout}"


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
    """Off by default has to be *cheap* by default, not merely quiet.

    This is now the counterfactual's perf guard as well, and that is the more
    demanding case: the measurement runs on Reads that are *not* being
    replaced, so a repo with the feature off pays it on every qualifying read
    and gets no tokens back. It has to stay on the same cheap import graph as
    the path it stands in for.
    """
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

    # Guard the guard, same as the opted-in case: if the counterfactual stopped
    # running, the import assertion above would pass by doing nothing at all.
    from repowise.cli.commands.augment_cmd.read_state import _session_state_path

    state = json.loads(_session_state_path(repo, "perf").read_text("utf-8"))
    assert state["forgone"] == [rel], "the probe did not reach the counterfactual"


def _indexed_search_repo(tmp_path: Path) -> Path:
    """A repo the Grep hook will take all the way to a triage emission.

    Three tables, spelled as the fast lookups read them: the repository row
    they resolve the id from, the symbols the coverage leg needs, and the file
    nodes the PageRank leg needs. Written with stdlib sqlite3 for the same
    reason the hook now reads it that way.
    """
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("CREATE TABLE repositories (id TEXT, local_path TEXT)")
    con.execute("INSERT INTO repositories VALUES ('r1', ?)", (str(repo),))
    con.execute(
        "CREATE TABLE wiki_symbols (repository_id TEXT, file_path TEXT, name TEXT, "
        "kind TEXT, start_line INTEGER)"
    )
    con.execute(
        "INSERT INTO wiki_symbols VALUES ('r1', 'src/b.py', 'parse_yaml', 'function', 42)"
    )
    con.execute(
        "CREATE TABLE graph_nodes (repository_id TEXT, node_id TEXT, node_type TEXT, "
        "pagerank REAL)"
    )
    for node_id, pagerank in (("src/a.py", 0.9), ("src/b.py", 0.1)):
        con.execute("INSERT INTO graph_nodes VALUES ('r1', ?, 'file', ?)", (node_id, pagerank))
    con.commit()
    con.close()
    return repo


def test_a_triage_that_queries_the_index_imports_nothing_heavy(tmp_path: Path) -> None:
    """The surface that *does* reach the index, on the same cheap graph.

    Triage queried the wiki through the ORM, so a firing cost ~1.1s of cold
    ``repowise.core.persistence`` import against 34ms of query. It now runs on
    stdlib sqlite3. This is the guard that keeps it there, and unlike the
    silent-invocation test above it can only pass by actually emitting.
    """
    repo = _indexed_search_repo(tmp_path)
    content = "\n".join(
        f"src/{'a' if i % 2 else 'b'}.py:{i}:parse_yaml(x)" for i in range(1, 21)
    )
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Grep",
        "tool_input": {"pattern": "parse_yaml"},
        "tool_response": {"mode": "content", "content": content, "numLines": 20},
        "cwd": str(repo),
        "session_id": "perf",
    }
    code = (
        "import sys, json, io; "
        f"sys.stdin = io.StringIO({json.dumps(payload)!r}); "
        "from repowise.cli.commands.augment_cmd import _run_augment; "
        "_run_augment(client=None); "
        f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert "Most likely relevant" in out.stdout, "the probe did not reach a triage emission"
    assert out.stderr.strip() == "", f"a triage emission pulled in:\n{out.stderr}"


def test_a_wrong_path_rescue_imports_nothing_heavy(tmp_path: Path) -> None:
    """The failure surface reaches the index too, on the same cheap graph.

    Its basename lookup is the fourth caller of ``fast_lookup``; this is the
    guard that keeps it from reaching for the ORM the moment someone finds a
    query easier to write there. Like the triage guard, it can only pass by
    actually emitting.
    """
    repo = _indexed_search_repo(tmp_path)
    # The rescue only names a file it can still see on disk, so the indexed
    # node needs a real one behind it here.
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "b.py").write_text("x", encoding="utf-8")
    attempted = repo / "src" / "nested" / "b.py"
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Read",
        "tool_input": {"file_path": str(attempted)},
        "error": (
            f"<tool_use_error>Path does not exist: {attempted}. "
            f"Note: your current working directory is {repo}.</tool_use_error>"
        ),
        "cwd": str(repo),
        "session_id": "perf",
    }
    code = (
        "import sys, json, io; "
        f"sys.stdin = io.StringIO({json.dumps(payload)!r}); "
        "from repowise.cli.commands.augment_cmd import _run_augment; "
        "_run_augment(client=None); "
        f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert "is not in this tree" in out.stdout, "the probe did not reach a rescue"
    assert out.stderr.strip() == "", f"a wrong-path rescue pulled in:\n{out.stderr}"


def _reread_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo where the second Read of a file is collapsed to a notice."""
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_reread: true\n", encoding="utf-8"
    )
    rel = "big.py"
    source = "\n".join(f"line {i} with enough text to be worth not repeating" for i in range(200))
    (repo / rel).write_text(source, encoding="utf-8")
    return repo, rel, source


def test_a_collapsed_reread_imports_nothing_heavy(tmp_path: Path) -> None:
    """The re-read collapse never needs the index, so it must never reach it.

    It is a hash comparison over the payload the hook was already handed. If
    this ever pulls the skeleton builder or the ORM in, something has started
    doing work the surface does not need.
    """
    repo, rel, _ = _reread_repo(tmp_path)
    env = _fake_home(tmp_path)
    env["REPOWISE_HOOK_UPDATED_OUTPUT"] = "1"
    probe = _read_payload_probe(repo, rel)
    code = (
        probe
        + "sys.stdout.write('|SECOND|'); "
        + probe
        + f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    second = out.stdout.split("|SECOND|", 1)[1]
    assert "Unchanged since you read it" in second, "the probe did not reach the collapse"
    assert out.stderr.strip() == "", f"a collapsed re-read pulled in:\n{out.stderr}"


def test_a_glob_timeout_rescue_imports_nothing_heavy(tmp_path: Path) -> None:
    """The rescue answers a path query from stdlib sqlite3 and nothing else."""
    repo = _indexed_search_repo(tmp_path)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "a.py").write_text("x", encoding="utf-8")
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Glob",
        "tool_input": {"pattern": "src/*.py"},
        "error": (
            "Ripgrep search timed out after 20 seconds. The search may have matched "
            "files but did not complete in time. Try searching a more specific path "
            "or pattern."
        ),
        "cwd": str(repo),
        "session_id": "perf",
    }
    code = (
        "import sys, json, io; "
        f"sys.stdin = io.StringIO({json.dumps(payload)!r}); "
        "from repowise.cli.commands.augment_cmd import _run_augment; "
        "_run_augment(client=None); "
        f"heavy = sorted(m for m in sys.modules if m.startswith({_HEAVY_PREFIXES!r})); "
        "print('\\n'.join(heavy), file=sys.stderr)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=_fake_home(tmp_path),
    )
    assert "the index answered it instead" in out.stdout, "the probe did not reach the rescue"
    assert out.stderr.strip() == "", f"a glob timeout rescue pulled in:\n{out.stderr}"


def _modules_added_by(statement: str) -> set[str]:
    """Modules an import statement adds, over what the interpreter starts with.

    A delta rather than an absolute: this interpreter already has ``pathlib``
    resident before any repowise code runs (site processing pulls it), so an
    absolute check would either fail on something nobody imported or have to
    hard-code an allowlist that rots.
    """
    code = (
        "import sys; before = set(sys.modules); "
        f"{statement} "
        "print('\\n'.join(sorted(set(sys.modules) - before)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def test_the_shared_ledger_is_free_to_import() -> None:
    """``hook_ledger`` sits at the *module scope* of the PreToolUse rewrite hook.

    That hook fires on every shell command an agent runs, so importing the
    ledger has to cost nothing beyond what the hook already pays. ``sqlite3``
    is deferred into the functions that open a database, so a command that
    bails on shape never touches one.
    """
    added = _modules_added_by("import repowise.cli.hook_ledger;")
    assert "sqlite3" not in added, "importing the hook ledger pulled in sqlite3"


def test_the_rewrite_hook_still_opens_no_database_to_load() -> None:
    """The PreToolUse hook gained a ledger; it must not have gained a cost.

    It writes one counter row per shell command, and that write is deferred
    until after the response is on stdout. Loading the module must stay as
    cheap as it was before the ledger existed.
    """
    added = _modules_added_by("import repowise.cli.rewrite_hook;")
    for module in ("sqlite3", "click", "yaml"):
        assert module not in added, f"the rewrite hook pulled in {module} at import"


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
