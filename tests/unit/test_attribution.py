"""AI attribution consent settings, three-state labels and the honest share.

Covers the stricter-wins precedence between repo and user config, the
``DO_NOT_TRACK`` override, value validation, the coverage and suppression
arithmetic, git-ai v3 notes with ``h_`` human keys, Agent Trace human records,
and that ``attribution.enabled: false`` stops detection at index time.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from repowise.core import attribution
from repowise.core.attribution import (
    COVERAGE_FLOOR,
    SUPPRESSED_LOW_COVERAGE,
    SUPPRESSED_NO_COMMITS,
    attribution_enabled,
    attribution_share,
    commit_label,
    file_share,
    load_attribution_settings,
)
from repowise.core.ingestion.git_commit_index import load_commit_index, load_git_ai_note_agents
from repowise.core.ingestion.git_indexer.agent_provenance import (
    AgentProvenanceClassifier,
    AgentTraceIndex,
    classifier_from_repo_config,
    parse_git_ai_note,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private user config location, and no DO_NOT_TRACK from the shell."""
    user_home = tmp_path / "home"
    (user_home / ".repowise").mkdir(parents=True)
    user_file = user_home / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    return user_home


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, config: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    if config is not None:
        _write(repo / ".repowise" / "config.yaml", config)
    return repo


# ---------------------------------------------------------------------------
# Settings precedence
# ---------------------------------------------------------------------------


def test_defaults_read_markers_and_capture_nothing(home: Path, tmp_path: Path) -> None:
    settings = load_attribution_settings(_repo(tmp_path))
    assert settings.enabled is True
    assert settings.capture == "off"
    assert settings.agents == ()
    assert settings.errors == ()


@pytest.mark.parametrize(
    ("repo_value", "user_value"), [("false", None), (None, "false"), ("false", "true")]
)
def test_enabled_false_in_either_file_wins(
    home: Path, tmp_path: Path, repo_value: str | None, user_value: str | None
) -> None:
    if user_value is not None:
        _write(home / ".repowise" / "config.yaml", f"attribution:\n  enabled: {user_value}\n")
    repo_cfg = f"attribution:\n  enabled: {repo_value}\n" if repo_value is not None else None
    settings = load_attribution_settings(_repo(tmp_path, repo_cfg))
    assert settings.enabled is False
    assert settings.capture == "off"


@pytest.mark.parametrize(
    ("repo_level", "user_level", "effective"),
    [
        ("notes", "local", "local"),
        ("local", "notes", "local"),
        ("notes", "notes", "notes"),
        ("notes", None, "off"),  # the user never opted in
        (None, "notes", "off"),  # the repo never opted in
    ],
)
def test_capture_takes_the_lower_level(
    home: Path, tmp_path: Path, repo_level: str | None, user_level: str | None, effective: str
) -> None:
    if user_level:
        _write(home / ".repowise" / "config.yaml", f"attribution:\n  capture: {user_level}\n")
    repo_cfg = f"attribution:\n  capture: {repo_level}\n" if repo_level else None
    settings = load_attribution_settings(_repo(tmp_path, repo_cfg))
    assert settings.capture == effective


@pytest.mark.parametrize("value", ["off", "no", "false", "OFF", "'off'"])
def test_yaml_boolean_off_reads_as_off(home: Path, tmp_path: Path, value: str) -> None:
    settings = load_attribution_settings(_repo(tmp_path, f"attribution:\n  capture: {value}\n"))
    assert settings.capture == "off"
    assert settings.repo.capture == "off"
    assert settings.errors == ()


@pytest.mark.parametrize("value", ["on", "yes", "true"])
def test_yaml_boolean_true_capture_is_invalid(home: Path, tmp_path: Path, value: str) -> None:
    _write(home / ".repowise" / "config.yaml", "attribution:\n  capture: local\n")
    settings = load_attribution_settings(_repo(tmp_path, f"attribution:\n  capture: {value}\n"))
    assert settings.capture == "off"
    assert len(settings.errors) == 1
    assert "use local or notes" in settings.errors[0]


def test_agents_are_the_intersection(home: Path, tmp_path: Path) -> None:
    _write(
        home / ".repowise" / "config.yaml",
        "attribution:\n  capture: local\n  agents: [claude-code, codex]\n",
    )
    repo = _repo(tmp_path, "attribution:\n  capture: local\n  agents: [codex, cursor]\n")
    settings = load_attribution_settings(repo)
    assert settings.agents == ("codex",)
    assert settings.repo.agents == ("codex", "cursor")
    assert settings.user.agents == ("claude-code", "codex")


def test_do_not_track_forces_capture_off(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(home / ".repowise" / "config.yaml", "attribution:\n  capture: notes\n  agents: [codex]\n")
    repo = _repo(tmp_path, "attribution:\n  capture: notes\n  agents: [codex]\n")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    settings = load_attribution_settings(repo)
    assert settings.do_not_track is True
    assert settings.capture == "off"
    assert settings.agents == ()
    # Reading public markers is not tracking; only capture is switched off.
    assert settings.enabled is True


def test_invalid_values_fall_to_the_strictest(home: Path, tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "attribution:\n  enabled: maybe\n  capture: everything\n  agents: [codex, not-an-agent]\n",
    )
    settings = load_attribution_settings(repo, known_agents=["codex", "claude-code"])
    assert settings.enabled is False
    assert settings.capture == "off"
    assert settings.repo.agents == ("codex",)
    assert len(settings.errors) == 3
    assert any("not-an-agent" in e for e in settings.errors)


def test_unreadable_repo_config_disables_attribution(home: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path, "attribution: [unclosed\n")
    settings = load_attribution_settings(repo)
    assert settings.enabled is False
    assert settings.errors


def test_attribution_enabled_follows_config_edits(home: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert attribution_enabled(repo) is True
    _write(repo / ".repowise" / "config.yaml", "attribution:\n  enabled: false\n")
    assert attribution_enabled(repo) is False
    assert attribution_enabled(None) is True
    assert attribution_enabled(MagicMock()) is True  # not a path: user file only


# ---------------------------------------------------------------------------
# Three-state labels and coverage math
# ---------------------------------------------------------------------------


def test_commit_label_never_reads_absence_as_human() -> None:
    assert commit_label(None, None) == "unknown"
    assert commit_label(None, "claude") == "agent"
    assert commit_label("human_attested", None) == "human_attested"
    assert commit_label("unknown", None) == "unknown"


def test_share_is_of_covered_commits_with_its_coverage() -> None:
    share = attribution_share(agent=30, human_attested=10, total=100)
    assert share.coverage == pytest.approx(0.4)
    assert share.agent_share == pytest.approx(0.75)
    assert share.unknown == 60
    assert share.suppressed_reason is None
    assert share.summary() == "at least 75% of covered commits, coverage 40%"


def test_share_is_withheld_below_the_floor() -> None:
    share = attribution_share(agent=2, human_attested=0, total=1000)
    assert share.coverage == pytest.approx(0.002)
    assert share.agent_share is None
    assert share.suppressed_reason == SUPPRESSED_LOW_COVERAGE
    assert share.summary().startswith("withheld")


def test_share_at_the_floor_is_shown() -> None:
    share = attribution_share(agent=3, human_attested=0, total=10)
    assert share.coverage == pytest.approx(COVERAGE_FLOOR)
    assert share.agent_share == 1.0


def test_share_without_commits() -> None:
    share = attribution_share(0, 0, 0)
    assert share.coverage is None
    assert share.suppressed_reason == SUPPRESSED_NO_COMMITS


def test_file_share_reads_rows_written_before_the_known_count() -> None:
    # An older row has the agent count but a zero known count.
    share = file_share(agent_commit_count=4, known_count=0, total=10)
    assert share.agent == 4
    assert share.human_attested == 0
    assert share.coverage == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# git-ai v3 notes
# ---------------------------------------------------------------------------

_HUMAN_ONLY_NOTE = """src/app.py
  h_0123456789abcd 1-12,20
  h_fedcba98765432 30
---
{"schema_version": "authorship/3.0.0", "git_ai_version": "1.7.5",
 "base_commit_sha": "deadbeef", "prompts": {},
 "humans": {"h_0123456789abcd": {"author": "Jane Doe <jane@example.com>"}}}"""

_MIXED_NOTE = """src/app.py
  h_0123456789abcd 1-4
  s_0011223344aabb::t_99887766554433 5-30
---
{"schema_version": "authorship/3.0.0", "base_commit_sha": "deadbeef", "prompts": {},
 "sessions": {"s_0011223344aabb": {"agent_id": {"tool": "claude-code", "id": "x", "model": "m"}}}}"""

_LEGACY_PROMPT_NOTE = """src/app.py
  0123456789abcdef 1-9
---
{"schema_version": "authorship/3.0.0", "base_commit_sha": "deadbeef",
 "prompts": {"0123456789abcdef": {"agent_id": {"tool": "cursor", "id": "y", "model": "m"}}}}"""

_UNRESOLVED_AI_NOTE = """src/app.py
  h_0123456789abcd 1-4
  0123456789abcdef 5-9
---
{"schema_version": "authorship/3.0.0", "base_commit_sha": "deadbeef", "prompts": {}}"""


def test_git_ai_note_with_only_human_keys_attests_human() -> None:
    note = parse_git_ai_note(_HUMAN_ONLY_NOTE)
    assert note.agent is None
    assert note.human is True


def test_git_ai_note_with_ai_session_is_agent() -> None:
    note = parse_git_ai_note(_MIXED_NOTE)
    assert note.agent == "claude"
    assert note.human is False


def test_git_ai_legacy_prompt_note_is_agent() -> None:
    assert parse_git_ai_note(_LEGACY_PROMPT_NOTE).agent == "cursor"


def test_git_ai_note_with_an_unresolved_ai_key_is_not_human() -> None:
    note = parse_git_ai_note(_UNRESOLVED_AI_NOTE)
    assert note.agent is None
    assert note.human is False


@pytest.mark.parametrize("raw", ["", "no divider", "x\n---\nnot json", "x\n---\n[1, 2]"])
def test_malformed_git_ai_note_is_no_signal(raw: str) -> None:
    note = parse_git_ai_note(raw)
    assert (note.agent, note.human) == (None, False)


def test_notes_loader_keeps_human_attestations(tmp_path: Path, home: Path) -> None:
    repo = MagicMock()
    repo.working_tree_dir = str(_repo(tmp_path))
    repo.git.for_each_ref.return_value = "abc commit\trefs/notes/ai"
    repo.git.log.return_value = (
        f"\x00sha_h\x1f{_HUMAN_ONLY_NOTE}\n\x00sha_a\x1f{_MIXED_NOTE}\n\x00sha_none\x1f\n"
    )
    notes = load_git_ai_note_agents(repo, 50)
    assert notes["sha_h"].human is True
    assert notes["sha_a"].agent == "claude"
    assert "sha_none" not in notes


def test_notes_loader_reads_nothing_when_disabled(tmp_path: Path, home: Path) -> None:
    repo = MagicMock()
    repo.working_tree_dir = str(_repo(tmp_path, "attribution:\n  enabled: false\n"))
    assert load_git_ai_note_agents(repo, 50) == {}
    repo.git.for_each_ref.assert_not_called()


# ---------------------------------------------------------------------------
# Classifier: three states and the off switch
# ---------------------------------------------------------------------------

CLF = AgentProvenanceClassifier()


def test_unmarked_commit_is_unknown_not_human() -> None:
    prov = CLF.classify("Dev", "dev@x.com", "Dev", "dev@x.com", "feat: x")
    assert prov.agent is None
    assert prov.attribution == "unknown"


def test_human_note_attests_human() -> None:
    prov = CLF.classify("Dev", "dev@x.com", "Dev", "dev@x.com", "feat: x", note_human=True)
    assert (prov.attribution, prov.channel) == ("human_attested", "git_ai_note")


def test_agent_channel_beats_a_human_record() -> None:
    prov = CLF.classify(
        "Dev", "dev@x.com", "Dev", "dev@x.com", "x\n\nGenerated with Claude Code", trace_human=True
    )
    assert (prov.agent, prov.attribution) == ("claude", "agent")


def test_disabled_classifier_labels_nothing() -> None:
    off = AgentProvenanceClassifier(enabled=False)
    prov = off.classify("Dev", "dev@x.com", "Dev", "dev@x.com", "x\n\nGenerated with Claude Code")
    assert (prov.agent, prov.attribution) == (None, None)


def test_repo_config_off_yields_disabled_classifier(tmp_path: Path, home: Path) -> None:
    repo = _repo(tmp_path, "attribution:\n  enabled: false\n")
    assert classifier_from_repo_config(repo).enabled is False
    assert classifier_from_repo_config(_repo(tmp_path / "other")).enabled is True


def test_disabled_walk_stores_no_labels_or_rollup(tmp_path: Path, home: Path) -> None:
    import time
    from datetime import UTC, datetime

    from repowise.core.ingestion.git_indexer.file_history import index_file

    ts = int(time.time())
    iso = datetime.fromtimestamp(ts, UTC).isoformat()
    raw = (
        f"\x00aaa\x1fDev\x1fdev@x.com\x1fDev\x1fdev@x.com\x1f{ts}\x1f{iso}\x1f\x1ffix: x"
        f"\x1fGenerated with Claude Code\n1\t0\tsrc/a.py"
    )
    repo = MagicMock()
    repo.git.log.return_value = raw
    sink: list[dict] = []
    index = load_commit_index(
        repo,
        100,
        {"src/a.py"},
        commit_sink=sink,
        provenance_classifier=AgentProvenanceClassifier(enabled=False),
    )
    assert sink[0]["agent_name"] is None
    assert sink[0]["attribution"] is None
    meta = index_file(
        repo,
        "src/a.py",
        repo_path=MagicMock(),
        commit_limit=100,
        follow_renames=False,
        include_blame=False,
        precomputed_commits=index["src/a.py"],
    )
    assert meta["agent_commit_count"] == 0
    assert meta["attribution_known_count"] == 0
    assert meta["agent_authored_pct"] is None


# ---------------------------------------------------------------------------
# Agent Trace human records
# ---------------------------------------------------------------------------


def _trace(revision: str, kind: str, path: str = "src/a.py") -> str:
    return json.dumps(
        {
            "version": "0.1.0",
            "id": f"t-{revision}-{kind}",
            "timestamp": "2026-10-01T00:00:00Z",
            "vcs": {"type": "git", "revision": revision},
            "files": [
                {"path": path, "conversations": [{"contributor": {"type": kind}, "ranges": []}]}
            ],
        }
    )


def test_trace_human_record_attests_its_commit(tmp_path: Path, home: Path) -> None:
    repo_dir = _repo(tmp_path)
    _write(
        repo_dir / ".agent-trace" / "traces.jsonl",
        "\n".join([_trace("aaa", "human"), _trace("bbb", "unknown"), _trace("ccc", "ai")]),
    )
    repo = MagicMock()
    repo.working_tree_dir = str(repo_dir)
    index = AgentTraceIndex.load(repo)
    assert index.resolve_human("aaa", (), {"src/a.py"}) is True
    assert index.resolve_human("aaa", (), {"src/other.py"}) is False  # no path overlap
    assert index.resolve_human("bbb", (), {"src/a.py"}) is False  # unknown is not human
    assert index.resolve_human("ccc", (), {"src/a.py"}) is False


def test_trace_file_is_not_read_when_disabled(tmp_path: Path, home: Path) -> None:
    repo_dir = _repo(tmp_path, "attribution:\n  enabled: false\n")
    _write(repo_dir / ".agent-trace" / "traces.jsonl", _trace("ccc", "ai"))
    repo = MagicMock()
    repo.working_tree_dir = str(repo_dir)
    assert not AgentTraceIndex.load(repo)


# ---------------------------------------------------------------------------
# Human attestation must cover every changed path
# ---------------------------------------------------------------------------


def test_trace_human_needs_every_changed_path(tmp_path: Path, home: Path) -> None:
    repo_dir = _repo(tmp_path)
    _write(repo_dir / ".agent-trace" / "traces.jsonl", _trace("aaa", "human"))
    repo = MagicMock()
    repo.working_tree_dir = str(repo_dir)
    index = AgentTraceIndex.load(repo)
    assert index.resolve_human("aaa", (), {"src/a.py"}) is True
    # Partial overlap stays unknown: a false human is the worse error.
    assert index.resolve_human("aaa", (), {"src/a.py", "src/b.py"}) is False
    assert index.resolve_human("aaa", (), set()) is False


def _header(sha: str = "sha1") -> dict:
    return {
        "sha": sha,
        "parents": (),
        "author_name": "Dev",
        "author_email": "dev@x.com",
        "committer_name": "Dev",
        "committer_email": "dev@x.com",
        "subject": "feat: x",
        "body": "",
    }


def test_git_ai_human_note_needs_every_changed_path() -> None:
    from repowise.core.ingestion.git_indexer.agent_provenance import classify_commit

    notes = {"sha1": parse_git_ai_note(_HUMAN_ONLY_NOTE)}
    assert notes["sha1"].paths == frozenset({"src/app.py"})
    full, _ = classify_commit(CLF, _header(), {"src/app.py"}, notes, None)
    partial, _ = classify_commit(CLF, _header(), {"src/app.py", "src/other.py"}, notes, None)
    assert full.attribution == "human_attested"
    assert partial.attribution == "unknown"


def test_git_ai_divider_is_the_first_column_zero_rule() -> None:
    # A later "---" line after the metadata must not move the split.
    note = _HUMAN_ONLY_NOTE + "\n---\nnot json"
    assert parse_git_ai_note(note).human is True


def test_oversized_git_ai_note_is_ignored() -> None:
    padding = "".join(f"\n  h_{i:014x} 1" for i in range(60_000))
    note = "src/app.py" + padding + "\n---\n" + '{"prompts": {}}'
    assert len(note) > 1_000_000
    assert parse_git_ai_note(note) == parse_git_ai_note("")


# ---------------------------------------------------------------------------
# attribution.enabled: false clears what an earlier index stored
# ---------------------------------------------------------------------------


async def _seed_labelled_index(repo_dir: Path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from repowise.core.persistence import crud
    from repowise.core.persistence.database import init_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = await crud.upsert_repository(session, name="r", local_path=str(repo_dir))
        await crud.upsert_git_commits_bulk(
            session,
            repo.id,
            [
                {
                    "sha": "a" * 10,
                    "author_name": "A",
                    "author_email": "a@x.com",
                    "subject": "x",
                    "agent_name": "claude",
                    "agent_autonomy_tier": 2,
                    "attribution": "agent",
                }
            ],
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo.id,
            file_path="a.py",
            commit_count_total=1,
            agent_commit_count=1,
            attribution_known_count=1,
            agent_authored_pct=1.0,
            agent_line_count=7,
        )
        await session.commit()
    return engine, factory, repo.id


async def _ai_rows(factory, repo_id: str) -> tuple[list, list]:
    from sqlalchemy import select

    from repowise.core.persistence.models import GitCommit, GitMetadata

    async with factory() as session:
        commits = (
            await session.execute(
                select(GitCommit.agent_name, GitCommit.attribution).where(
                    GitCommit.repository_id == repo_id
                )
            )
        ).all()
        meta = (
            await session.execute(
                select(
                    GitMetadata.agent_commit_count,
                    GitMetadata.agent_authored_pct,
                    GitMetadata.agent_line_count,
                ).where(GitMetadata.repository_id == repo_id)
            )
        ).all()
    return [tuple(r) for r in commits], [tuple(r) for r in meta]


@pytest.mark.asyncio
async def test_update_after_switching_off_leaves_no_ai_rows(tmp_path: Path, home: Path) -> None:
    from repowise.core.pipeline.persist import persist_git_refresh

    repo_dir = _repo(tmp_path)
    engine, factory, repo_id = await _seed_labelled_index(repo_dir)
    try:
        # Still enabled: an update keeps what was derived.
        async with factory() as session:
            await persist_git_refresh(session, repo_id, {}, None, None)
            await session.commit()
        assert (await _ai_rows(factory, repo_id))[0] == [("claude", "agent")]

        _write(repo_dir / ".repowise" / "config.yaml", "attribution:\n  enabled: false\n")
        async with factory() as session:
            await persist_git_refresh(session, repo_id, {}, None, None)
            await session.commit()
        commits, meta = await _ai_rows(factory, repo_id)
        assert commits == [(None, None)]
        assert meta == [(0, None, 0)]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_index_with_attribution_off_stores_nothing(tmp_path: Path, home: Path) -> None:
    from types import SimpleNamespace

    from repowise.core.pipeline.persist import persist_git

    repo_dir = _repo(tmp_path, "attribution:\n  enabled: false\n")
    engine, factory, repo_id = await _seed_labelled_index(repo_dir)
    try:
        async with factory() as session:
            await persist_git(
                SimpleNamespace(git_metadata_list=[], git_summary=None), session, repo_id
            )
            await session.commit()
        commits, meta = await _ai_rows(factory, repo_id)
        assert commits == [(None, None)]
        assert meta == [(0, None, 0)]
    finally:
        await engine.dispose()
