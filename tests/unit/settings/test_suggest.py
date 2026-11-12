"""Settings suggestions: each predicate, the facts they read, and every surface."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.cli.ui.result_panels import SUGGESTED_STEP, build_contextual_next_steps
from repowise.core.analysis.decisions.manifest import ManifestDecision, write_manifest
from repowise.core.settings import all_settings, get
from repowise.core.settings.areas.hooks import (
    MIN_AGENT_SESSIONS,
    MIN_FORGONE_ITEMS,
    MIN_FORGONE_TOKENS,
)
from repowise.core.settings.suggest import RepoFacts, suggestions

#: The keys ``Suggestion.to_dict`` promises.
SUGGESTION_KEYS = {
    "schema_version", "key", "current_value", "suggested_value", "reason", "impact",
    "benefit", "command",
}  # fmt: skip

#: Every key a predicate may suggest; a new one is added here with its tests.
SUGGESTABLE = {
    "decisions.mode",
    "embedder",
    "hooks.coverage_reingest",
    "hooks.read_reread",
    "hooks.read_skeleton",
    "hooks.search_digest",
}

_EMBEDDER_ENVS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    "OLLAMA_EMBEDDING_MODEL", "EDENAI_API_KEY",
)  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for s in all_settings():
        if s.env is not None and not s.env.prefix:
            monkeypatch.delenv(s.env.name, raising=False)
    for name in _EMBEDDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    return root


def _suggest(key: str, **facts) -> object:
    return get(key).suggest_when(RepoFacts(**facts))


def _config(repo: Path, data: dict) -> None:
    (repo / ".repowise" / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _db(path: Path, *statements: tuple[str, tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    for sql, params in statements:
        con.execute(sql, params)
    con.commit()
    con.close()


def _forgone(repo: Path, flag: str, items: int, tokens_each: int, *, age_days: float = 0) -> None:
    created = time.time() - age_days * 86400
    _db(
        repo / ".repowise" / "omissions" / "omissions.db",
        (
            "CREATE TABLE IF NOT EXISTS forgone_savings (created_at REAL, source TEXT, "
            "path TEXT, raw_tokens INTEGER, distilled_tokens INTEGER, filter TEXT)",
            (),
        ),
        *(
            (
                "INSERT INTO forgone_savings VALUES (?, 'hook-read', ?, ?, 0, ?)",
                (created, f"{flag}/f{i}.py", tokens_each, flag),
            )
            for i in range(items)
        ),
    )


def _agent_sessions(repo: Path, count: int, *, age_days: float = 0) -> None:
    import os

    directory = repo / ".repowise" / "hook-sessions"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - age_days * 86400
    for i in range(count):
        path = directory / f"s{age_days}-{i}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (stamp, stamp))


def _coverage(repo: Path, table: str = "coverage_ingests") -> None:
    _db(
        repo / ".repowise" / "wiki.db",
        (f"CREATE TABLE {table} (id INTEGER, ingested_at TEXT)", ()),
        (f"INSERT INTO {table} VALUES (1, '2026-11-01')", ()),
    )


def _decision() -> ManifestDecision:
    return ManifestDecision(
        id="d1",
        title="Keep the hook off the ORM",
        decision="Hooks use stdlib sqlite3.",
        reason="The ORM import costs more than the hook budget.",
        scope=["packages/cli"],
        accepted_at="2026-11-01T00:00:00Z",
    )


def _keys(repo: Path) -> list[str]:
    return [s.key for s in suggestions(repo)]


# ---------------------------------------------------------------- predicates


def test_only_the_known_settings_carry_a_predicate():
    assert {s.key for s in all_settings() if s.suggest_when} == SUGGESTABLE


@pytest.mark.parametrize("flag", ["read_skeleton", "search_digest", "read_reread"])
def test_hook_surface_needs_a_clear_measured_saving(flag):
    key = f"hooks.{flag}"
    enough = (MIN_FORGONE_ITEMS, MIN_FORGONE_TOKENS)
    proposal = _suggest(key, forgone={flag: enough})
    assert proposal.value is True and proposal.benefit == "tokens"
    assert f"{MIN_FORGONE_TOKENS:,} fewer tokens" in proposal.reason
    assert _suggest(key) is None
    assert _suggest(key, forgone={flag: (MIN_FORGONE_ITEMS - 1, 10**6)}) is None
    assert _suggest(key, forgone={flag: (100, MIN_FORGONE_TOKENS - 1)}) is None
    other = next(f for f in ("read_skeleton", "search_digest") if f != flag)
    assert _suggest(key, forgone={other: enough}) is None


def test_coverage_reingest_needs_stored_coverage_and_running_hooks():
    key = "hooks.coverage_reingest"
    enough = MIN_AGENT_SESSIONS
    assert _suggest(key, coverage_ingested=True, agent_sessions=enough).value is True
    assert _suggest(key, coverage_ingested=False, agent_sessions=100) is None
    assert _suggest(key, coverage_ingested=True, agent_sessions=enough - 1) is None


def test_embedder_needs_docs_without_semantic_search_and_a_ready_embedder():
    off = {"full_text": "available", "semantic": "unavailable"}
    found = ("gemini", "GEMINI_API_KEY is set")
    proposal = _suggest("embedder", search=off, embedder_found=found)
    assert proposal.value == "gemini"
    assert proposal.command == "repowise reindex --embedder gemini"
    assert "GEMINI_API_KEY is set" in proposal.reason
    mock = {"embedder": "mock"}
    assert _suggest("embedder", search=off, embedder_found=found, values=mock).value == "gemini"
    assert _suggest("embedder", search=off) is None
    on = {**off, "semantic": "available"}
    assert _suggest("embedder", search=on, embedder_found=found) is None
    no_docs = {"full_text": "unavailable", "semantic": "unavailable"}
    assert _suggest("embedder", search=no_docs, embedder_found=found) is None
    chosen = {"embedder": "openai"}
    assert _suggest("embedder", search=off, embedder_found=found, values=chosen) is None


def test_embedder_spend_line_follows_the_provider_spec():
    off = {"full_text": "available", "semantic": "unavailable"}
    local = _suggest("embedder", search=off, embedder_found=("ollama", "a model is set"))
    assert "no spend" in local.impact
    keyed = _suggest("embedder", search=off, embedder_found=("gemini", "GEMINI_API_KEY is set"))
    assert "billed to your gemini key" in keyed.impact


def test_ollama_embedder_promises_no_spend():
    off = {"full_text": "available", "semantic": "unavailable"}
    found = ("ollama", "OLLAMA_EMBEDDING_MODEL is set")
    assert "no spend" in _suggest("embedder", search=off, embedder_found=found).impact


def test_decisions_mode_needs_tracked_decisions_switched_off():
    off = {"decisions.mode": "off"}
    proposal = _suggest("decisions.mode", tracked_decisions=3, values=off)
    assert proposal.value == "read_only" and "3 team decisions" in proposal.reason
    assert _suggest("decisions.mode", tracked_decisions=0, values=off) is None
    full = {"decisions.mode": "full"}
    assert _suggest("decisions.mode", tracked_decisions=3, values=full) is None


# ---------------------------------------------------------------- facts from disk


def test_a_default_repo_gets_no_suggestions(repo):
    assert suggestions(repo) == []


def test_a_repo_with_no_store_gets_no_suggestions(tmp_path):
    assert suggestions(tmp_path) == []


def test_a_relative_root_reads_the_same_facts(repo, monkeypatch):
    _forgone(repo, "read_skeleton", 50, 10_000)
    monkeypatch.chdir(repo.parent)
    assert _keys(Path(repo.name)) == ["hooks.read_skeleton"]


def test_a_forgone_table_without_the_filter_column_reads_as_none(repo):
    _db(
        repo / ".repowise" / "omissions" / "omissions.db",
        (
            "CREATE TABLE forgone_savings (created_at REAL, source TEXT, path TEXT, "
            "raw_tokens INTEGER, distilled_tokens INTEGER)",
            (),
        ),
    )
    assert suggestions(repo) == []


def test_measured_savings_on_disk_suggest_the_surface(repo):
    _forgone(repo, "read_skeleton", MIN_FORGONE_ITEMS, MIN_FORGONE_TOKENS // MIN_FORGONE_ITEMS)
    (found,) = suggestions(repo)
    assert found.key == "hooks.read_skeleton"
    assert found.current is False and found.value is True
    assert found.command == "repowise config set hooks.read_skeleton true"
    assert set(found.to_dict()) == SUGGESTION_KEYS


def test_old_measurements_do_not_count(repo):
    _forgone(repo, "read_skeleton", 50, 10_000, age_days=45)
    assert suggestions(repo) == []


def test_a_surface_already_on_is_not_suggested(repo):
    _forgone(repo, "search_digest", 50, 10_000)
    _config(repo, {"hooks": {"search_digest": True}})
    assert suggestions(repo) == []


def test_an_environment_override_is_left_alone(repo, monkeypatch):
    _forgone(repo, "read_skeleton", 50, 10_000)
    monkeypatch.setenv("REPOWISE_HOOK_READ_SKELETON", "0")
    assert suggestions(repo) == []


@pytest.mark.parametrize("table", ["coverage_ingests", "coverage_files"])
def test_coverage_and_recent_agent_sessions_suggest_reingest(repo, table):
    _coverage(repo, table)
    _agent_sessions(repo, MIN_AGENT_SESSIONS, age_days=10)
    assert suggestions(repo) == []
    _agent_sessions(repo, MIN_AGENT_SESSIONS)
    assert _keys(repo) == ["hooks.coverage_reingest"]


def test_agent_sessions_alone_suggest_nothing(repo):
    _agent_sessions(repo, 50)
    assert suggestions(repo) == []


def test_tracked_decisions_on_disk_suggest_read_only(repo):
    from dataclasses import replace

    write_manifest(repo, [replace(_decision(), currency="superseded")])
    _config(repo, {"decisions": {"mode": "off"}})
    assert suggestions(repo) == []
    (repo / ".repowise" / "config.yaml").unlink()
    write_manifest(repo, [_decision()])
    assert suggestions(repo) == []
    _config(repo, {"decisions": {"mode": "off"}})
    (found,) = suggestions(repo)
    assert (found.key, found.current, found.value) == ("decisions.mode", "off", "read_only")


def test_a_keyless_index_with_a_key_now_suggests_the_embedder(repo, monkeypatch):
    state = {
        "index_scope": {"search": {"full_text": "available", "semantic": "unavailable"}},
        "docs_mode": "deterministic",
    }
    (repo / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _config(repo, {"embedder": "mock"})
    assert suggestions(repo) == []
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    (found,) = suggestions(repo)
    assert (found.key, found.value) == ("embedder", "gemini")
    assert found.command == "repowise reindex --embedder gemini"


def test_a_broken_config_suggests_nothing(repo):
    _forgone(repo, "read_skeleton", 50, 10_000)
    (repo / ".repowise" / "config.yaml").write_text("hooks: [unclosed", encoding="utf-8")
    assert suggestions(repo) == []


def test_suggestions_stay_cheap(repo):
    _forgone(repo, "read_skeleton", 200, 1_000)
    _agent_sessions(repo, 20)
    _coverage(repo)
    suggestions(repo)  # imports
    start = time.perf_counter()
    for _ in range(5):
        suggestions(repo)
    assert (time.perf_counter() - start) / 5 < 0.25


# ---------------------------------------------------------------- surfaces


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli, list(args))
    return result.exit_code, result.output


def _payload(out: str) -> dict:
    return json.loads(out[out.index("{") :])


def test_config_list_suggested_json_and_text(repo):
    path = str(repo)
    code, out = _invoke("config", "list", "--suggested", "--path", path, "--format", "json")
    assert code == 0 and _payload(out)["suggestions"] == []
    code, out = _invoke("config", "list", "--suggested", "--path", path)
    assert "Every setting fits this repository" in out

    _forgone(repo, "read_skeleton", 50, 10_000)
    code, out = _invoke("config", "list", "--suggested", "--path", path, "--format", "json")
    (entry,) = _payload(out)["suggestions"]
    assert set(entry) == SUGGESTION_KEYS
    assert entry["suggested_value"] is True and entry["benefit"] == "tokens"
    code, out = _invoke("config", "list", "--suggested", "--path", path)
    assert "hooks.read_skeleton" in out and "--path" in out
    code, out = _invoke(
        "config", "list", "--suggested", "--area", "decisions", "--path", path, "--format", "json"
    )
    assert _payload(out)["suggestions"] == []


def test_doctor_and_status_name_the_count_once(repo):
    path = str(repo)
    _, out = _invoke("doctor", path, "--no-workspace", "--quick")
    assert "fits this repo" not in out
    _, out = _invoke("doctor", path, "--no-workspace", "--quick", "--format", "json")
    assert _payload(out)["suggestions"] == []

    _forgone(repo, "read_skeleton", 50, 10_000)
    _, out = _invoke("doctor", path, "--no-workspace", "--quick")
    assert out.count("1 option fits this repo: repowise config list --suggested") == 1
    _, out = _invoke("doctor", path, "--no-workspace", "--quick", "--format", "json")
    assert [s["key"] for s in _payload(out)["suggestions"]] == ["hooks.read_skeleton"]
    _, out = _invoke("status", path, "--no-workspace", "--format", "json")
    assert [s["key"] for s in _payload(out)["suggestions"]] == ["hooks.read_skeleton"]


def _empty_index(repo: Path) -> None:
    import asyncio

    from repowise.core.persistence import create_engine, init_db

    async def build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(build())


def test_status_text_names_the_count(repo, monkeypatch):
    from repowise.cli.commands import status_cmd

    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    _empty_index(repo)
    monkeypatch.setattr(status_cmd, "suggested_for", lambda root: [object(), object()])
    _, out = _invoke("status", str(repo), "--no-workspace")
    assert out.count("2 options fit this repo") == 1
    monkeypatch.setattr(status_cmd, "suggested_for", lambda root: [])
    _, out = _invoke("status", str(repo), "--no-workspace")
    assert "fit this repo" not in out


def test_init_panel_gives_suggestions_one_row_within_the_limit():
    steps = build_contextual_next_steps(suggested=2, dead_unused=4, hotspot_count=1)
    assert [command for command, _ in steps][1] == SUGGESTED_STEP
    assert "2 settings fit this repo" in steps[1][1]
    assert len(steps) == 3
    steps = build_contextual_next_steps(dead_unused=4)
    assert SUGGESTED_STEP not in [command for command, _ in steps]
    fast = build_contextual_next_steps(fast_mode=True, suggested=1, dead_unused=4)
    assert [command for command, _ in fast][2] == SUGGESTED_STEP
