"""Which harnesses the session lane reads, and what changes when it reads two.

Only Claude Code is read by default. A harness added to the registry is a
reader this repository has not asked for, and switching one on makes a
machine's whole history for that agent eligible on the next update.
"""

from __future__ import annotations

import json

import yaml

from repowise.core.analysis.decisions.policy import DEFAULT_HARNESSES, resolve_policy
from repowise.core.sessions.miners.decisions import harnesses_for


def _repo(tmp_path, block=None):
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    if block is not None:
        (repo / ".repowise" / "config.yaml").write_text(
            yaml.safe_dump({"decisions": block}), encoding="utf-8"
        )
    return repo


def test_only_claude_code_is_read_by_default(tmp_path):
    assert harnesses_for(_repo(tmp_path)) == ("claude_code",)
    assert DEFAULT_HARNESSES == ("claude_code",)


def test_a_named_harness_is_read(tmp_path):
    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})

    assert harnesses_for(repo) == ("claude_code", "codex")


def test_an_unknown_harness_is_dropped_with_a_warning():
    resolution = resolve_policy({"decisions": {"harnesses": ["claude_code", "emacs"]}})

    assert resolution.policy.harnesses == ("claude_code",)
    assert any("emacs" in w for w in resolution.warnings)


def test_naming_no_known_harness_falls_back_rather_than_reading_nothing():
    """An empty reader list is indistinguishable from a repo with no sessions."""
    resolution = resolve_policy({"decisions": {"harnesses": ["emacs"]}})

    assert resolution.policy.harnesses == DEFAULT_HARNESSES
    assert any("no known harness" in w for w in resolution.warnings)


def test_harnesses_is_a_known_key():
    resolution = resolve_policy({"decisions": {"harnesses": ["codex"]}})

    assert not any("Unknown key" in w for w in resolution.warnings)


def test_a_non_list_is_refused_rather_than_coerced():
    resolution = resolve_policy({"decisions": {"harnesses": "codex"}})

    assert resolution.policy.harnesses == DEFAULT_HARNESSES
    assert any("not a list" in w for w in resolution.warnings)


def test_duplicates_collapse_so_a_transcript_is_not_read_twice(tmp_path):
    repo = _repo(tmp_path, {"harnesses": ["codex", "codex", "claude_code"]})

    assert harnesses_for(repo) == ("codex", "claude_code")


def test_the_policy_payload_names_the_harnesses():
    """``decision config show`` and the API read this dict."""
    policy = resolve_policy({"decisions": {"harnesses": ["codex"]}}).policy

    assert policy.to_dict(provider_available=False)["harnesses"] == ["codex"]


def test_a_staged_observation_records_which_harness_it_came_off(tmp_path):
    from repowise.core.sessions.staging import SessionStagingStore

    store = SessionStagingStore(tmp_path / "sessions.db")
    store.add_raw(
        hash_="h1", kind="explicit_choice", quotes=["q"], files=[], session_id="s1",
        harness="codex", now=1.0,
    )
    row = store._conn.execute("SELECT harness FROM raw_candidates WHERE hash = 'h1'").fetchone()

    assert row[0] == "codex"


def test_a_store_written_before_the_column_still_opens(tmp_path):
    """The ALTER runs on open; a pre-existing sidecar must not be a crash."""
    import sqlite3

    from repowise.core.sessions.staging import SessionStagingStore

    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE raw_candidates (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, "
        "quotes TEXT NOT NULL, files TEXT NOT NULL, session_id TEXT, "
        "created_at REAL NOT NULL, structured_key TEXT);"
    )
    conn.execute(
        "INSERT INTO raw_candidates VALUES ('old', 'explicit_choice', '[]', '[]', 's0', 1.0, NULL)"
    )
    conn.commit()
    conn.close()

    store = SessionStagingStore(db)
    rows = dict(store._conn.execute("SELECT hash, harness FROM raw_candidates").fetchall())

    # Empty rather than backfilled: those rows' attribution cannot be recovered.
    assert rows == {"old": ""}


# --- the drive loop -------------------------------------------------------


def _claude_transcript(repo_root, projects_root, name, session_id, text):
    from repowise.core.sessions.adapters.claude_code import transcript_dir_for

    directory = transcript_dir_for(repo_root, projects_root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps(
            {
                "type": "user",
                "cwd": str(repo_root),
                "timestamp": "2026-07-11T10:00:00.000Z",
                "sessionId": session_id,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )


async def test_the_default_config_never_reads_the_codex_root(tmp_path, monkeypatch):
    """The safety property of shipping this off: nothing new is read.

    Codex discovery is not repo-scoped, so a harness switched on by accident
    makes every session on the machine eligible. Default means untouched.
    """
    from repowise.core.sessions.miners.decisions import mine_session_decisions
    from repowise.core.sessions.staging import SessionStagingStore, default_store_path

    repo = _repo(tmp_path)
    projects_root = tmp_path / "projects"
    _claude_transcript(repo, projects_root, "a.jsonl", "s1", "No, use the venv python because bare python is stale")

    seen: list[str] = []
    from repowise.core.sessions.adapters import codex as codex_mod

    original = codex_mod.CodexAdapter.discover

    def spy(self, repo_root, *, projects_root=None):
        seen.append("codex")
        return original(self, repo_root, projects_root=projects_root)

    monkeypatch.setattr(codex_mod.CodexAdapter, "discover", spy)

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=100.0)

    assert seen == []
    # Anchored to a run that did something, or the negative proves nothing.
    store = SessionStagingStore(default_store_path(repo))
    assert store._conn.execute("SELECT COUNT(*) FROM raw_candidates").fetchone()[0] == 1


def _codex_rollout(projects_root, repo, name, *, turn_id=None, subdir="codex"):
    """A native Codex rollout, inside the sandbox the caller was given."""
    root = projects_root / subdir / "2026" / "07" / "11" if subdir else projects_root / "2026" / "07" / "11"
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "timestamp": "2026-07-11T10:00:00.000Z",
        "type": "session_meta",
        "payload": {"session_id": "cx-1", "cwd": str(repo), "originator": "codex-tui"},
    }
    payload = {
        "type": "user_message",
        "message": "No, use the venv python because bare python is a stale install",
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    turn = {"timestamp": "2026-07-11T10:00:01.000Z", "type": "event_msg", "payload": payload}
    # Compact, like a real rollout: the raw-string gates match `"type":"x"`
    # with no space after the colon, which is what Codex writes.
    compact = (",", ":")
    (root / name).write_text(
        json.dumps(meta, separators=compact) + "\n" + json.dumps(turn, separators=compact) + "\n",
        encoding="utf-8",
    )


def _staged_by_harness(repo):
    from repowise.core.sessions.staging import SessionStagingStore, default_store_path

    store = SessionStagingStore(default_store_path(repo))
    return dict(
        store._conn.execute(
            "SELECT harness, COUNT(*) FROM raw_candidates GROUP BY harness"
        ).fetchall()
    )


async def test_both_harnesses_are_read_when_both_are_named(tmp_path):
    from repowise.core.sessions.miners.decisions import mine_session_decisions

    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})
    projects_root = tmp_path / "projects"
    _claude_transcript(repo, projects_root, "a.jsonl", "s1", "No, use the venv python because bare python is stale")
    _codex_rollout(projects_root, repo, "rollout-x.jsonl")

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=100.0)
    staged = _staged_by_harness(repo)

    assert staged.get("claude_code"), staged
    assert staged.get("codex"), staged


async def test_a_sandboxed_run_never_escapes_to_the_real_home(tmp_path, monkeypatch):
    """``projects_root`` is a sandbox, and a second harness must stay inside it.

    A harness that falls back to the real home reads the machine's whole
    history for that agent into a temporary store, which is slow, undeterministic
    and a privacy surprise on a developer box.
    """
    from repowise.core.sessions.miners.decisions import mine_session_decisions

    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})
    projects_root = tmp_path / "projects"
    _claude_transcript(repo, projects_root, "a.jsonl", "s1", "No, use the venv python because bare python is stale")

    # A rollout at the real home location, which the run must not reach.
    home = tmp_path / "home"
    _codex_rollout(home / ".codex" / "sessions", repo, "escaped.jsonl", subdir=None)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=100.0)
    staged = _staged_by_harness(repo)

    assert staged.get("claude_code"), staged
    assert "codex" not in staged, staged


async def test_an_imported_session_is_not_staged_by_the_drive_loop(tmp_path):
    """End to end: the latch holds through discovery, cursoring and mining."""
    from repowise.core.sessions.miners.decisions import mine_session_decisions

    repo = _repo(tmp_path, {"harnesses": ["codex"]})
    projects_root = tmp_path / "projects"
    _codex_rollout(projects_root, repo, "imported.jsonl", turn_id="external-import-turn-1")

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=100.0)

    assert _staged_by_harness(repo) == {}


async def test_a_resumed_read_still_scopes_to_this_repo(tmp_path):
    """The cwd fix has to survive the cursor, which is the common path.

    Codex states cwd once, at the top of the file, and a resumed read starts
    past it. If the adapter does not recover it, every appended line from
    every session on the machine reads as belonging to whichever repo is
    being indexed.
    """
    from repowise.core.sessions.miners.decisions import mine_session_decisions

    repo = _repo(tmp_path, {"harnesses": ["codex"]})
    other = tmp_path / "elsewhere"
    other.mkdir()
    projects_root = tmp_path / "projects"
    # The rollout belongs to another repo entirely.
    _codex_rollout(projects_root, other, "rollout-y.jsonl")

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=100.0)

    rollout = projects_root / "codex" / "2026" / "07" / "11" / "rollout-y.jsonl"
    appended = {
        "timestamp": "2026-07-11T10:00:02.000Z",
        "type": "event_msg",
        "payload": {
            "type": "user_message",
            "message": "No, always use ruff check because the format sweep buries the diff",
        },
    }
    with rollout.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(appended, separators=(",", ":")) + "\n")

    await mine_session_decisions(repo, provider=None, projects_root=projects_root, now=200.0)

    assert _staged_by_harness(repo) == {}


def test_a_non_default_harness_list_round_trips_through_a_policy_write(tmp_path):
    """Every config mutation validates by round-tripping the written block.

    A field on the policy that the block does not carry fails that check on
    every write, so the setting bricks the commands that configure it.
    """
    from repowise.core.analysis.decisions.policy_store import load_policy, write_policy

    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})
    policy = load_policy(repo).policy

    write_policy(repo, policy.with_llm(False))

    assert load_policy(repo).policy.harnesses == ("claude_code", "codex")


def test_applying_a_preset_keeps_the_harness_list(tmp_path):
    """A preset names source membership, not which harnesses are read."""
    from dataclasses import replace

    from repowise.core.analysis.decisions.policy import preset_policy
    from repowise.core.analysis.decisions.policy_store import load_policy, write_policy

    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})
    current = load_policy(repo).policy

    write_policy(repo, replace(preset_policy("local_only"), harnesses=current.harnesses))

    assert load_policy(repo).policy.harnesses == ("claude_code", "codex")


def test_a_non_default_harness_list_is_not_a_named_preset():
    """Otherwise the one setting that widens what is read reads as `default`."""
    policy = resolve_policy({"decisions": {"harnesses": ["claude_code", "codex"]}}).policy

    assert policy.preset_name() == "custom"


async def test_the_callers_policy_wins_over_a_second_config_read(tmp_path):
    """One resolved answer, not two that can disagree.

    Both pipelines resolve the policy before calling the miner. Re-reading
    config here would be a second answer to a question already asked, and the
    two can drift.
    """
    from repowise.core.sessions.miners.decisions import mine_session_decisions

    # Config names codex; the caller passes claude_code only. The caller wins.
    repo = _repo(tmp_path, {"harnesses": ["claude_code", "codex"]})
    projects_root = tmp_path / "projects"
    _claude_transcript(repo, projects_root, "a.jsonl", "s1", "No, use the venv python because bare python is stale")
    _codex_rollout(projects_root, repo, "rollout-x.jsonl")

    await mine_session_decisions(
        repo,
        provider=None,
        projects_root=projects_root,
        harnesses=("claude_code",),
        now=100.0,
    )
    staged = _staged_by_harness(repo)

    assert staged.get("claude_code"), staged
    assert "codex" not in staged, staged


def test_an_unregistered_harness_from_a_caller_is_dropped_not_raised():
    """A config written against a newer repowise must not stop this one."""
    from repowise.core.sessions.miners.decisions import registered_harnesses

    assert registered_harnesses(["codex", "emacs"]) == ("codex",)
    assert registered_harnesses(["emacs"]) == DEFAULT_HARNESSES
