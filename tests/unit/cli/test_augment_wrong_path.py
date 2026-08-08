"""The wrong-path rescue: what it says, and the eight ways it stays quiet.

Weighted towards silence on purpose. The surface's whole value is that a
rescue can be trusted, so every rule that drops a candidate has a test naming
the failure it prevents — measured against 435 transcripts of this repo, where
86 path-not-found failures on the file tools reduce to 18 this speaks to.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from repowise.cli.commands.augment_cmd import _run_augment
from repowise.cli.commands.augment_cmd.wrong_path import _handle_tool_failure


def _run_hook(payload: dict, tmp_path: Path) -> str:
    """Drive the real stdin/stdout entry point, so the dispatch is under test.

    ``TMPDIR`` is redirected because ``_emit_response`` dedupes identical
    emissions for 8 seconds through a marker in the *system* temp directory,
    keyed on the text alone. The rescue text is all repo-relative paths, so
    two runs of this test inside 8 seconds would collide with each other and
    the second would see silence.
    """
    out = io.StringIO()
    env = {v: str(tmp_path) for v in ("TMPDIR", "TEMP", "TMP")}
    with patch.dict(os.environ, env), patch.object(
        sys, "stdin", io.StringIO(json.dumps(payload))
    ), patch.object(sys, "stdout", out):
        tempfile.tempdir = None
        _run_augment(client=None)
    tempfile.tempdir = None
    return out.getvalue()

PATH_ERROR = (
    "<tool_use_error>Path does not exist: {path}. "
    "Note: your current working directory is {cwd}.</tool_use_error>"
)
FILE_ERROR = "File does not exist. Note: your current working directory is {cwd}"


def _repo(tmp_path: Path, files: tuple[str, ...] = ()) -> Path:
    """A repo whose index knows *files*, and whose disk carries them too."""
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("CREATE TABLE repositories (id TEXT, local_path TEXT)")
    con.execute("INSERT INTO repositories VALUES ('r1', ?)", (str(repo),))
    con.execute(
        "CREATE TABLE graph_nodes (repository_id TEXT, node_id TEXT, node_type TEXT, "
        "pagerank REAL)"
    )
    for node_id in files:
        con.execute("INSERT INTO graph_nodes VALUES ('r1', ?, 'file', 0.5)", (node_id,))
        on_disk = repo / node_id
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_text("x", encoding="utf-8")
    con.commit()
    con.close()
    return repo


def _fire(repo: Path, attempted: str, *, tool: str = "Read", error: str | None = None):
    if error is None:
        error = PATH_ERROR.format(path=attempted, cwd=repo)
    return _handle_tool_failure(
        tool,
        {"file_path": attempted},
        error,
        str(repo),
        session_id="s1",
    )


# --- the one thing it says -------------------------------------------------


def test_names_the_only_file_carrying_that_basename(tmp_path: Path) -> None:
    """The measured shape: a real file, guessed under the wrong directory."""
    repo = _repo(tmp_path, ("core/ingestion/git_indexer/fix_events.py",))
    result = _fire(repo, str(repo / "core" / "git_indexer" / "fix_events.py"))
    assert result.context == (
        "[repowise] core/git_indexer/fix_events.py is not in this tree. "
        "The only indexed fix_events.py is core/ingestion/git_indexer/fix_events.py"
    )


def test_a_relative_attempt_is_resolved_against_cwd(tmp_path: Path) -> None:
    """The tool resolved it against cwd, so the rescue has to as well."""
    repo = _repo(tmp_path, ("src/deep/config_loader.py",))
    result = _fire(repo, "src/config_loader.py")
    assert result.context is not None
    assert "src/deep/config_loader.py" in result.context


def test_the_grep_error_shape_is_read_too(tmp_path: Path) -> None:
    """Grep names the path in the error; Read does not. Both must work."""
    repo = _repo(tmp_path, ("packages/ui/shared/table.tsx",))
    result = _handle_tool_failure(
        "Grep",
        {"pattern": "x", "path": str(repo / "packages" / "table.tsx")},
        PATH_ERROR.format(path=repo / "packages" / "table.tsx", cwd=repo),
        str(repo),
        session_id="s1",
    )
    assert result.context is not None
    assert "packages/ui/shared/table.tsx" in result.context


def test_the_read_error_shape_takes_the_path_from_tool_input(tmp_path: Path) -> None:
    """"File does not exist." carries no path; tool_input is the only source."""
    repo = _repo(tmp_path, ("a/b/widget.py",))
    result = _fire(
        repo, str(repo / "a" / "widget.py"), error=FILE_ERROR.format(cwd=repo)
    )
    assert result.context is not None
    assert "a/b/widget.py" in result.context


# --- and the ways it does not ---------------------------------------------


def test_an_ambiguous_basename_stays_silent(tmp_path: Path) -> None:
    """Naming one of three registry.py confidently is the failure to avoid."""
    repo = _repo(tmp_path, ("a/registry.py", "b/registry.py", "c/registry.py"))
    assert not _fire(repo, str(repo / "core" / "registry.py"))


def test_a_directory_target_stays_silent(tmp_path: Path) -> None:
    """"Which file did you mean" is not the question a directory asks."""
    repo = _repo(tmp_path, ("core/hooks/handler.py",))
    assert not _fire(repo, str(repo / "cli" / "hooks"))


def test_a_path_in_another_checkout_stays_silent(tmp_path: Path) -> None:
    """A sibling worktree has its own index; this one cannot answer for it."""
    repo = _repo(tmp_path, ("packages/types/src/overview.ts",))
    other = tmp_path / "repowise-restyle" / "packages" / "types" / "overview.ts"
    assert not _fire(repo, str(other))


def test_a_relative_escape_to_another_checkout_stays_silent(tmp_path: Path) -> None:
    """The same sibling, spelled relatively, must not slip past containment.

    A character-set ``lstrip("./")`` turned "../repowise-restyle/x.ts" into a
    path that looked repo-relative, and this tree answered for the other one.
    """
    repo = _repo(tmp_path, ("packages/types/src/overview.ts",))
    assert not _fire(repo, "../repowise-restyle/packages/types/overview.ts")


def test_a_dot_directory_keeps_its_dot(tmp_path: Path) -> None:
    """".github/ci.yml" must not be echoed back as "github/ci.yml"."""
    repo = _repo(tmp_path, (".github/workflows/ci.yml",))
    result = _fire(repo, ".github/ci.yml")
    assert result.context is not None
    assert result.context.startswith("[repowise] .github/ci.yml is not in this tree.")


def test_a_repo_under_a_path_with_spaces_still_rescues(tmp_path: Path) -> None:
    """A checkout under "Foo Bar" must not silence the surface entirely.

    Every absolute path in such a tree contains a space, so a blanket
    whitespace guard takes recall to zero rather than reducing it.
    """
    spaced = tmp_path / "Foo Bar"
    spaced.mkdir()
    repo = _repo(spaced, ("core/deep/fix_events.py",))
    result = _fire(repo, str(repo / "core" / "fix_events.py"))
    assert result.context is not None
    assert "core/deep/fix_events.py" in result.context


def test_a_stale_row_does_not_hide_the_live_answer(tmp_path: Path) -> None:
    """Two indexed rows, one deleted, is still one live answer."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("INSERT INTO graph_nodes VALUES ('r1', 'old/fix_events.py', 'file', 0.5)")
    con.commit()
    con.close()
    result = _fire(repo, str(repo / "core" / "fix_events.py"))
    assert result.context is not None
    assert "core/deep/fix_events.py" in result.context


def test_an_external_package_node_is_not_a_path(tmp_path: Path) -> None:
    """graph_nodes carries resolved external packages under node_type 'file'."""
    repo = _repo(tmp_path, ("src/real/config.py",))
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("INSERT INTO graph_nodes VALUES ('r1', 'external:vendor/config.py', 'file', 0.5)")
    con.commit()
    con.close()
    result = _fire(repo, str(repo / "src" / "config.py"))
    assert result.context is not None
    assert "src/real/config.py" in result.context


def test_an_error_that_already_suggests_stays_silent(tmp_path: Path) -> None:
    """Claude Code printed its own answer; repeating it is worse than nothing."""
    repo = _repo(tmp_path, ("src/shared/responsive-table.tsx",))
    error = FILE_ERROR.format(cwd=repo) + " Did you mean responsive-table?"
    assert not _fire(repo, str(repo / "responsive-table.tsx"), error=error)


def test_it_never_restates_the_path_that_just_failed(tmp_path: Path) -> None:
    """The index can hold a row for a file that is not on disk right now."""
    repo = _repo(tmp_path)
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("INSERT INTO graph_nodes VALUES ('r1', 'src/gone.py', 'file', 0.5)")
    con.commit()
    con.close()
    assert not _fire(repo, str(repo / "src" / "gone.py"))


def test_an_indexed_row_with_no_file_on_disk_stays_silent(tmp_path: Path) -> None:
    """A stale row must not send the agent to a path that is also missing."""
    repo = _repo(tmp_path)
    con = sqlite3.connect(repo / ".repowise" / "wiki.db")
    con.execute("INSERT INTO graph_nodes VALUES ('r1', 'src/deep/gone.py', 'file', 0.5)")
    con.commit()
    con.close()
    assert not _fire(repo, str(repo / "src" / "gone.py"))


def test_several_paths_in_one_grep_argument_stay_silent(tmp_path: Path) -> None:
    """The basename of whichever landed last is not what was asked for."""
    repo = _repo(tmp_path, ("tests/unit/cli/test_one.py",))
    both = f"{repo / 'tests' / 'test_two.py'} {repo / 'tests' / 'test_one.py'}"
    assert not _fire(repo, both, tool="Grep")


def test_an_unresolvable_basename_stays_silent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("src/other.py",))
    assert not _fire(repo, str(repo / "src" / "missing.py"))


def test_a_bash_failure_is_not_this_surface(tmp_path: Path) -> None:
    """Its input is a command line, not a path; a different problem."""
    repo = _repo(tmp_path, ("src/deep/thing.py",))
    result = _handle_tool_failure(
        "Bash",
        {"command": "cat src/thing.py"},
        "Exit code 1\ncat: src/thing.py: No such file or directory",
        str(repo),
        session_id="s1",
    )
    assert not result


def test_a_failure_that_is_not_about_a_path_stays_silent(tmp_path: Path) -> None:
    """Edit's commonest failure names no path at all."""
    repo = _repo(tmp_path, ("src/deep/thing.py",))
    result = _handle_tool_failure(
        "Edit",
        {"file_path": str(repo / "src" / "deep" / "thing.py")},
        "<tool_use_error>String to replace not found in file.\nString: foo",
        str(repo),
        session_id="s1",
    )
    assert not result


def test_no_index_stays_silent(tmp_path: Path) -> None:
    """No wiki.db is the ordinary state of a repo that never ran init."""
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    assert not _fire(repo, str(repo / "src" / "thing.py"))


def test_a_dict_error_is_unwrapped(tmp_path: Path) -> None:
    """The rejection comes through verbatim from the tool, not the harness."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    attempted = str(repo / "core" / "fix_events.py")
    result = _handle_tool_failure(
        "Read",
        {"file_path": attempted},
        {"message": PATH_ERROR.format(path=attempted, cwd=repo)},
        str(repo),
        session_id="s1",
    )
    assert result.context is not None


def test_an_error_shape_we_cannot_read_stays_silent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    assert not _handle_tool_failure(
        "Read",
        {"file_path": str(repo / "core" / "fix_events.py")},
        {"code": 42},
        str(repo),
        session_id="s1",
    )


# --- the event, end to end -------------------------------------------------


def test_the_hook_reads_the_error_field_not_tool_response(tmp_path: Path) -> None:
    """PostToolUseFailure carries ``error``; ``tool_response`` is PostToolUse
    only, and reading the wrong one is a surface that never fires."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    attempted = str(repo / "core" / "fix_events.py")
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Read",
        "tool_input": {"file_path": attempted},
        "error": PATH_ERROR.format(path=attempted, cwd=repo),
        "cwd": str(repo),
        "session_id": "s1",
    }
    assert "is not in this tree" in _run_hook(payload, tmp_path)


def test_an_interrupt_is_not_a_failure_to_rescue(tmp_path: Path) -> None:
    """The event fires when the user presses escape too. Answering that is
    noise at the exact moment someone asked for quiet."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    attempted = str(repo / "core" / "fix_events.py")
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Read",
        "tool_input": {"file_path": attempted},
        "error": PATH_ERROR.format(path=attempted, cwd=repo),
        "is_interrupt": True,
        "cwd": str(repo),
        "session_id": "s1",
    }
    assert _run_hook(payload, tmp_path) == ""


# --- the ledger ------------------------------------------------------------


def test_a_firing_is_recorded_under_its_own_surface(tmp_path: Path) -> None:
    """A surface that cannot report an efficacy rate does not ship."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    assert _fire(repo, str(repo / "core" / "fix_events.py"))
    con = sqlite3.connect(repo / ".repowise" / "sessions" / "sessions.db")
    rows = con.execute(
        "SELECT surface, category, chars FROM injections WHERE session_id = 's1'"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    surface, category, chars = rows[0]
    assert (surface, category) == ("wrong_path", "rescue")
    assert chars > 0


def test_the_same_rescue_twice_in_a_session_logs_once(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    attempted = str(repo / "core" / "fix_events.py")
    assert _fire(repo, attempted)
    assert _fire(repo, attempted)
    con = sqlite3.connect(repo / ".repowise" / "sessions" / "sessions.db")
    (count,) = con.execute(
        "SELECT COUNT(*) FROM injections WHERE session_id = 's1'"
    ).fetchone()
    con.close()
    assert count == 1


def test_no_session_id_still_rescues(tmp_path: Path) -> None:
    """Measurement is a sidecar; losing it must not cost the enrichment."""
    repo = _repo(tmp_path, ("core/deep/fix_events.py",))
    result = _handle_tool_failure(
        "Read",
        {"file_path": str(repo / "core" / "fix_events.py")},
        PATH_ERROR.format(path=repo / "core" / "fix_events.py", cwd=repo),
        str(repo),
        session_id="",
    )
    assert result.context is not None
