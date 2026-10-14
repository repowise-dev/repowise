"""Commit binding precision: a miss always beats a false hit."""

from __future__ import annotations

import time

from repowise.core.edit_capture.binder import bind_pending
from repowise.core.edit_capture.edits import CapturedCall, FileEdit
from repowise.core.edit_capture.store import (
    EVENTS,
    append_jsonl,
    read_jsonl,
    record_call,
    sidecar_dir,
)

BODY = [
    "def settle(ledger):",
    "    balance = sum(entry.amount for entry in ledger)",
    "    fees = compute_fees(balance, ledger.currency)",
    "    return balance - fees",
]


def _agent_edit(repo, path: str, lines: list[str], agent: str = "claude_code", session: str = "s1") -> None:
    call = CapturedCall(tool="Edit", edits=[FileEdit(path, lines)], session_id=session, cwd=str(repo.root))
    assert record_call(repo.root, agent, call) == 1


def test_agent_lines_bind_to_the_commit(repo) -> None:
    _agent_edit(repo, "src/settle.py", BODY)
    repo.write("src/settle.py", "\n".join(BODY) + "\n")
    sha = repo.commit("settle")
    (binding,) = bind_pending(repo.root, capture="local")
    assert binding["commit"] == sha and binding["agent"] == "claude_code"
    assert binding["sessions"][0]["files"] == {"src/settle.py": [1, 2, 3, 4]}
    # consumed: nothing pending any more
    assert read_jsonl(sidecar_dir(repo.root) / EVENTS) == []


def test_bound_commit_feeds_three_state_provenance(repo, user_config) -> None:
    import git as gitpython

    from repowise.core.ingestion.git_commit_index import load_git_ai_note_agents

    _agent_edit(repo, "src/settle.py", BODY)
    repo.write("src/settle.py", "\n".join(BODY) + "\n")
    sha = repo.commit("settle")
    bind_pending(repo.root, capture="local")
    on = "attribution:\n  capture: local\n  agents: [claude-code]\n"
    repo.configure(on)
    user_config.write_text(on, encoding="utf-8")
    notes = load_git_ai_note_agents(gitpython.Repo(repo.root), None)
    assert notes[sha].agent == "claude" and notes[sha].source == "edit_capture"
    # Withdrawing consent stops the stored bindings from labelling anything.
    user_config.write_text("attribution:\n  capture: off\n", encoding="utf-8")
    assert load_git_ai_note_agents(gitpython.Repo(repo.root), None) == {}
    user_config.write_text(on, encoding="utf-8")
    # The init commit had no capture: absent, i.e. unknown, never human.
    assert all(not n.human for n in notes.values()) and len(notes) == 1


def test_capture_off_binds_nothing(repo) -> None:
    _agent_edit(repo, "src/settle.py", BODY)
    repo.write("src/settle.py", "\n".join(BODY) + "\n")
    repo.commit("settle")
    assert bind_pending(repo.root, capture="off") == []
    assert len(read_jsonl(sidecar_dir(repo.root) / EVENTS)) == 1


def test_boilerplate_collision_is_a_miss(repo) -> None:
    """The agent wrote short, common lines; a human commits the same ones."""
    _agent_edit(repo, "src/a.py", ["    }", "    return None", "", "    pass", "else:"])
    repo.write("src/a.py", "    }\n    return None\n\n    pass\nelse:\n")
    repo.commit("human")
    assert bind_pending(repo.root, capture="local") == []


def test_repeated_line_cannot_anchor(repo) -> None:
    line = "    self.assertEqual(response.status_code, 200)"
    _agent_edit(repo, "tests/t.py", [line, line, line])
    repo.write("tests/t.py", f"{line}\n{line}\n{line}\n")
    repo.commit("tests")
    assert bind_pending(repo.root, capture="local") == []


def test_same_lines_in_another_file_do_not_bind(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/b.py", "\n".join(BODY) + "\n")
    repo.commit("copy")
    assert bind_pending(repo.root, capture="local") == []


def test_a_single_line_edit_is_a_miss(repo) -> None:
    _agent_edit(repo, "src/a.py", [BODY[1]])
    repo.write("src/a.py", BODY[1] + "\n")
    repo.commit("one")
    assert bind_pending(repo.root, capture="local") == []


def test_lines_two_agents_claim_are_ambiguous(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY, agent="claude_code")
    _agent_edit(repo, "src/a.py", BODY, agent="codex")
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("both")
    assert bind_pending(repo.root, capture="local") == []


def test_human_rewrite_after_the_agent_is_not_bound(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    rewritten = [BODY[0], "    total = sum(e.amount for e in ledger)", "    return total"]
    repo.write("src/a.py", "\n".join(rewritten) + "\n")
    repo.commit("rewrote")
    assert bind_pending(repo.root, capture="local") == []


def test_crlf_capture_binds_lf_commit(repo) -> None:
    _agent_edit(repo, "src/a.py", [line + "\r" for line in BODY])
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("lf")
    assert len(bind_pending(repo.root, capture="local")) == 1


def test_lf_capture_binds_crlf_commit(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\r\n".join(BODY) + "\r\n")
    repo.commit("crlf")
    assert len(bind_pending(repo.root, capture="local")) == 1


def test_windows_absolute_path_lands_repo_relative(repo) -> None:
    win = str(repo.root / "src" / "a.py").replace("/", "\\")
    _agent_edit(repo, win, BODY)
    (event,) = read_jsonl(sidecar_dir(repo.root) / EVENTS)
    assert event["path"] == "src/a.py"


def test_renamed_file_binds_through_the_rename(repo) -> None:
    base = "".join(f"value_{i} = {i}\n" for i in range(20))
    repo.write("src/old.py", base)
    repo.commit("old")
    _agent_edit(repo, "src/old.py", BODY)
    repo.git("mv", "src/old.py", "src/new.py")
    repo.write("src/new.py", base + "\n".join(BODY) + "\n")
    sha = repo.commit("rename")
    (binding,) = bind_pending(repo.root, capture="local")
    assert binding["commit"] == sha
    assert list(binding["sessions"][0]["files"]) == ["src/new.py"]


def test_a_bound_hash_cannot_bind_twice(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("first")
    assert len(bind_pending(repo.root, capture="local")) == 1
    repo.write("src/a.py", "\n".join(BODY) + "\n\n" + "\n".join(BODY) + "\n")
    repo.commit("human duplicate")
    assert bind_pending(repo.root, capture="local") == []


def test_an_event_from_after_the_commit_does_not_bind(repo) -> None:
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("human first")
    _agent_edit(repo, "src/a.py", BODY)
    path = sidecar_dir(repo.root) / EVENTS
    (event,) = read_jsonl(path)
    event["ts"] = time.time() + 3600
    path.unlink()
    append_jsonl(path, [event])
    assert bind_pending(repo.root, capture="local", now=time.time() + 3600) == []


def test_expired_events_are_dropped(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("late")
    assert bind_pending(repo.root, capture="local", now=time.time() + 30 * 86400) == []
    assert read_jsonl(sidecar_dir(repo.root) / EVENTS) == []


def test_binding_stops_at_the_cursor(repo) -> None:
    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("bound")
    assert len(bind_pending(repo.root, capture="local")) == 1
    other = [line.replace("ledger", "journal") for line in BODY]
    _agent_edit(repo, "src/b.py", other)
    repo.write("src/b.py", "\n".join(other) + "\n")
    sha = repo.commit("second")
    (binding,) = bind_pending(repo.root, capture="local")
    assert binding["commit"] == sha


def test_capped_run_resumes_past_the_first_batch(repo) -> None:
    from repowise.core.edit_capture.binder import MAX_COMMITS

    _agent_edit(repo, "src/a.py", BODY)
    for i in range(MAX_COMMITS):
        repo.git("commit", "-q", "--allow-empty", "-m", f"filler {i}")
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    sha = repo.commit("agent")
    for i in range(3):
        repo.git("commit", "-q", "--allow-empty", "-m", f"after {i}")
    # The first run spends its budget on the oldest commits and stops there.
    assert bind_pending(repo.root, capture="local") == []
    assert len(read_jsonl(sidecar_dir(repo.root) / EVENTS)) == 1
    (binding,) = bind_pending(repo.root, capture="local")
    assert binding["commit"] == sha


def test_bind_repo_uses_the_effective_level(repo, user_config) -> None:
    from repowise.core.edit_capture.binder import bind_repo

    _agent_edit(repo, "src/a.py", BODY)
    repo.write("src/a.py", "\n".join(BODY) + "\n")
    repo.commit("agent")
    assert bind_repo(repo.root) == []  # nobody opted in
    cfg = "attribution:\n  capture: local\n  agents: [claude-code]\n"
    repo.configure(cfg)
    user_config.write_text(cfg, encoding="utf-8")
    assert len(bind_repo(repo.root)) == 1
