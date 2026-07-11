"""Relevance-ranked decision injection: SessionStart block + edit-time notice.

The contract under test is "relevance or silence": a decision reaches the
agent only when the working set (or a repo-wide session rule) justifies it,
under a hard token cap, and the edit-time notice fires once per session per
decision under a strict cap. The wiki.db is built through the real ORM schema
so the hook's raw SQL is exercised against the true column names.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.augment_cmd import decision_inject
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    Repository,
)

_REPO_ID = "repo1"


async def _build_wiki_db(repo_root: Path, decisions: list[dict], extras=None) -> None:
    """Create .repowise/wiki.db via the real schema and insert test rows."""
    db_path = repo_root / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(repo_root)))
        for spec in decisions:
            session.add(
                DecisionRecord(
                    id=spec["id"],
                    repository_id=_REPO_ID,
                    title=spec["title"],
                    decision=spec.get("decision", ""),
                    rationale=spec.get("rationale", ""),
                    status=spec.get("status", "active"),
                    source=spec.get("source", "cli"),
                    confidence=spec.get("confidence", 0.9),
                    staleness_score=spec.get("staleness", 0.0),
                    evidence_file=spec["id"],  # keeps the unique constraint happy
                )
            )
            for node_id, link_type in spec.get("links", []):
                session.add(
                    DecisionNodeLink(
                        repository_id=_REPO_ID,
                        decision_id=spec["id"],
                        node_id=node_id,
                        link_type=link_type,
                    )
                )
            for sess in spec.get("evidence_sessions", []):
                session.add(
                    DecisionEvidence(
                        decision_id=spec["id"],
                        source="session",
                        evidence_commit=sess,
                        source_quote="q",
                    )
                )
        for extra in extras or []:
            session.add(extra)
        await session.commit()
    await engine.dispose()


def _quiet_git(monkeypatch, *, dirty=None, branch="main", branch_files=None) -> None:
    monkeypatch.setattr(
        decision_inject, "_dirty_files_and_branch", lambda p: (list(dirty or []), branch)
    )
    monkeypatch.setattr(
        decision_inject, "_branch_changed_files", lambda p, b: list(branch_files or [])
    )


_AUTH_DECISION = {
    "id": "d-auth",
    "title": "Use JWT auth",
    "decision": "All service auth uses short-lived JWT tokens",
    "rationale": "session cookies broke the mobile clients",
    "links": [("src/core/auth.py", "file")],
    "evidence_sessions": ["sess-a", "sess-b"],
}
_UNRELATED_DECISION = {
    "id": "d-unrelated",
    "title": "Batch the embed calls",
    "decision": "Embeddings are sent in batches of 64",
    "links": [("src/other/embed.py", "file")],
}


async def test_seed_linked_decision_injected(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION, _UNRELATED_DECISION])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")

    assert block is not None
    assert "Standing decisions" in block
    assert "Use JWT auth" in block
    assert "because session cookies broke the mobile clients" in block
    assert "Batch the embed calls" not in block


async def test_silence_when_nothing_relevant(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_UNRELATED_DECISION])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_silence_without_wiki_db(tmp_path):
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_proposed_and_dismissed_never_injected(tmp_path, monkeypatch):
    await _build_wiki_db(
        tmp_path,
        [
            {**_AUTH_DECISION, "id": "d-prop", "status": "proposed"},
            {**_AUTH_DECISION, "id": "d-dis", "title": "Dismissed rule", "status": "dismissed"},
        ],
    )
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_global_session_rule_injected_without_file_overlap(tmp_path, monkeypatch):
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-global",
                "title": "Never use em dashes",
                "decision": "never use em dashes in any output",
                "source": "session",
                "confidence": 0.8,
                "links": [],
            },
            _UNRELATED_DECISION,
        ],
    )
    _quiet_git(monkeypatch)  # no seeds at all

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Never use em dashes" in block
    assert "Batch the embed calls" not in block


async def test_global_rules_are_capped_and_never_crowd_out_linked(tmp_path, monkeypatch):
    """Unlinked rules are always eligible, so a cap keeps them from flooding
    the block; a working-set-linked decision must still get through."""
    rules = [
        {
            "id": f"d-g{i}",
            "title": f"Global rule {i}",
            "decision": f"always follow global rule number {i}",
            "source": "session",
            "confidence": 0.8,
            "links": [],
        }
        for i in range(5)
    ]
    await _build_wiki_db(tmp_path, [*rules, _AUTH_DECISION])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Use JWT auth" in block
    assert sum("Global rule" in ln for ln in block.splitlines()) == 2


async def test_unlinked_non_session_decision_is_not_global(tmp_path, monkeypatch):
    """Only session-mined rules get the repo-wide base relevance."""
    await _build_wiki_db(
        tmp_path,
        [{"id": "d-cli", "title": "A CLI note", "decision": "some note", "links": []}],
    )
    _quiet_git(monkeypatch)
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_one_hop_expansion_via_graph_edge(tmp_path, monkeypatch):
    hop_decision = {
        "id": "d-hop",
        "title": "Tokens rotate hourly",
        "decision": "token refresh happens on an hourly schedule",
        "confidence": 0.95,
        "links": [("src/core/token.py", "file")],
    }
    edge = GraphEdge(
        repository_id=_REPO_ID,
        source_node_id="src/core/auth.py",
        target_node_id="src/core/token.py",
    )
    await _build_wiki_db(tmp_path, [hop_decision], extras=[edge])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Tokens rotate hourly" in block


async def test_one_hop_expansion_via_cochange_partner(tmp_path, monkeypatch):
    partner_decision = {
        "id": "d-co",
        "title": "Schema and parser move together",
        "decision": "schema changes always update the parser in the same PR",
        "confidence": 0.95,
        "links": [("src/core/parser.py", "file")],
    }
    meta = GitMetadata(
        repository_id=_REPO_ID,
        file_path="src/core/auth.py",
        co_change_partners_json=json.dumps(
            [{"file_path": "src/core/parser.py", "co_change_count": 7}]
        ),
    )
    await _build_wiki_db(tmp_path, [partner_decision], extras=[meta])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Schema and parser move together" in block


async def test_branch_tokens_match_decision_text(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    _quiet_git(monkeypatch, branch="feat/jwt-rotation")  # no file seeds

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Use JWT auth" in block


async def test_previous_session_edits_seed_the_set(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    (tmp_path / ".repowise" / ".augment-session.json").write_text(
        json.dumps({"session_id": "old", "edits": {"src/core/auth.py": 3}}),
        encoding="utf-8",
    )
    _quiet_git(monkeypatch)

    block = decision_inject._session_decision_block(tmp_path, "sess-2")
    assert block is not None
    assert "Use JWT auth" in block


async def test_token_cap_and_item_cap(tmp_path, monkeypatch):
    long_text = "this decision line pads the token budget " * 8
    decisions = [
        {
            "id": f"d-{i}",
            "title": f"Decision number {i}",
            "decision": long_text,
            "links": [("src/core/auth.py", "file")],
        }
        for i in range(10)
    ]
    await _build_wiki_db(tmp_path, decisions)
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    lines = block.splitlines()
    assert len(lines) - 1 <= decision_inject._MAX_ITEMS
    assert decision_inject._estimate_tokens(block) <= decision_inject._TOKEN_CAP


async def test_sessionstart_injections_recorded(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    assert decision_inject._session_decision_block(tmp_path, "sess-9") is not None

    conn = sqlite3.connect(tmp_path / ".repowise" / "sessions" / "sessions.db")
    rows = conn.execute(
        "SELECT session_id, decision_id, node_id, evaluated FROM injections"
    ).fetchall()
    conn.close()
    assert rows == [("sess-9", "d-auth", "", 0)]


async def test_no_recording_without_session_id(tmp_path, monkeypatch):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    assert decision_inject._session_decision_block(tmp_path, "") is not None
    assert not (tmp_path / ".repowise" / "sessions" / "sessions.db").exists()


# ---------------------------------------------------------------------------
# Edit-time notice
# ---------------------------------------------------------------------------


async def test_edit_notice_fires_once_per_decision(tmp_path):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    state: dict = {}

    notice = decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", state)
    assert notice is not None
    assert "governed by a standing decision" in notice
    assert "Use JWT auth" in notice
    assert "because session cookies broke the mobile clients" in notice
    assert "confirmed across 2 sessions" in notice

    # Same decision again this session: silence.
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", state) is None


async def test_edit_notice_module_link_prefix_match(tmp_path):
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-mod",
                "title": "Core stays sync",
                "decision": "no asyncio inside src/core",
                "links": [("src/core", "module")],
            }
        ],
    )
    state: dict = {}
    notice = decision_inject._edit_decision_notice(tmp_path, "src/core/deep/file.py", "s1", state)
    assert notice is not None
    assert "Core stays sync" in notice
    # Not a prefix match: src/core_extra must not count as src/core.
    assert decision_inject._edit_decision_notice(tmp_path, "src/core_extra/f.py", "s1", {}) is None


async def test_edit_notice_dedup_survives_state_loss(tmp_path):
    """The sidecar claim, not the racy JSON state, is the real dedup.

    Two concurrent hook processes race read-modify-write on the state file
    and can lose the decisions_shown entry; a fresh state dict simulates
    that. The atomic INSERT OR IGNORE must still keep the notice single.
    """
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", {}) is not None
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", {}) is None


async def test_edit_notice_matches_backslash_stored_links(tmp_path):
    """Windows extraction stores link node ids with backslashes."""
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-win",
                "title": "Keep the CLI stdlib only",
                "decision": "hook-path modules import only the stdlib",
                "links": [("src\\cli\\hook.py", "file")],
            }
        ],
    )
    notice = decision_inject._edit_decision_notice(tmp_path, "src/cli/hook.py", "s1", {})
    assert notice is not None
    assert "Keep the CLI stdlib only" in notice


async def test_top_level_module_link_never_fires(tmp_path):
    """A link to a root module like `packages` is an extraction artifact."""
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-root",
                "title": "namespace, batched like the pages",
                "decision": "namespace, batched like the pages",
                "links": [("packages", "module")],
            }
        ],
    )
    assert (
        decision_inject._edit_decision_notice(tmp_path, "packages/cli/src/x.py", "s1", {}) is None
    )
    _quiet = decision_inject._session_decision_block  # scoring path, same guard
    # Seed inside the "governed" tree must still not surface it.
    import unittest.mock as mock

    with (
        mock.patch.object(
            decision_inject,
            "_dirty_files_and_branch",
            lambda p: (["packages/cli/src/x.py"], "main"),
        ),
        mock.patch.object(decision_inject, "_branch_changed_files", lambda p, b: []),
    ):
        assert _quiet(tmp_path, "s1") is None


async def test_echoed_title_is_not_repeated(tmp_path):
    """Legacy rows carry the same text in title/decision/rationale."""
    text = "namespace, batched like the pages. Uses embed_batch directly"
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-echo",
                "title": text,
                "decision": text + " (which raises on failure)",
                "rationale": text,
                "links": [("src/core/embed.py", "file")],
            }
        ],
    )
    notice = decision_inject._edit_decision_notice(tmp_path, "src/core/embed.py", "s1", {})
    assert notice is not None
    assert notice.count("namespace, batched") == 1
    assert "because" not in notice


async def test_edit_notice_respects_session_cap(tmp_path):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    state = {"decisions_shown": ["x", "y", "z"]}
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", state) is None


async def test_edit_notice_silent_for_ungoverned_file(tmp_path):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    assert decision_inject._edit_decision_notice(tmp_path, "README.md", "s1", {}) is None


async def test_edit_notice_records_injection(tmp_path):
    await _build_wiki_db(tmp_path, [_AUTH_DECISION])
    decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "sess-7", {})
    conn = sqlite3.connect(tmp_path / ".repowise" / "sessions" / "sessions.db")
    rows = conn.execute("SELECT session_id, decision_id, node_id FROM injections").fetchall()
    conn.close()
    assert rows == [("sess-7", "d-auth", "src/core/auth.py")]


# ---------------------------------------------------------------------------
# Seed plumbing details (pure helpers)
# ---------------------------------------------------------------------------


def test_branch_tokens_drop_workflow_words():
    assert decision_inject._branch_tokens("feat/decision-injection") == [
        "decision",
        "injection",
    ]
    assert decision_inject._branch_tokens("main") == []
    assert decision_inject._branch_tokens("fix/wip") == []


def test_dirty_files_parses_porcelain_branch_and_renames(tmp_path, monkeypatch):
    monkeypatch.setattr(
        decision_inject,
        "_git_lines",
        lambda p, *a: [
            "## feat/x...origin/main [ahead 2]",
            " M src/a.py",
            "?? new dir/",
            "R  old.py -> new.py",
            'A  "sp ace.py"',
        ],
    )
    files, branch = decision_inject._dirty_files_and_branch(tmp_path)
    assert files == ["src/a.py", "new.py", "sp ace.py"]
    assert branch == "feat/x"


def test_detached_head_yields_no_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        decision_inject, "_git_lines", lambda p, *a: ["## HEAD (no branch)", " M a.py"]
    )
    files, branch = decision_inject._dirty_files_and_branch(tmp_path)
    assert files == ["a.py"]
    assert branch == ""
