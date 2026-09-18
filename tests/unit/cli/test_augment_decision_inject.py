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
from repowise.core.analysis.decisions.lifecycle import (
    AGREEMENT_KIND,
    ARCHITECTURAL_KIND,
)
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionAcceptance,
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
                    kind=spec.get("kind", ARCHITECTURAL_KIND),
                    source=spec.get("source", "cli"),
                    confidence=spec.get("confidence", 0.9),
                    staleness_score=spec.get("staleness", 0.0),
                    evidence_file=spec["id"],  # keeps the unique constraint happy
                )
            )
            # Injection serves accepted decisions. A spec is accepted unless it
            # says otherwise, so an ``accepted: False`` spec is a candidate and
            # proves the hook leaves candidates out.
            if spec.get("accepted", True):
                session.add(
                    DecisionAcceptance(
                        repository_id=_REPO_ID,
                        decision_id=spec["id"],
                        seq=1,
                        action="accepted",
                        currency="active",
                        reason=spec.get("rationale") or spec["title"],
                        scope_json=json.dumps([spec["id"]]),
                        evidence_json=json.dumps([spec["id"]]),
                        accepter="tester",
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


async def test_an_accepted_record_off_status_reaches_neither_lane(tmp_path, monkeypatch):
    """Accepted, but not ``active``: not a standing decision and not a candidate.

    Both specs carry an acceptance row, so neither is candidate material; and
    neither is ``active``, so neither is a standing decision. A store in this
    shape is inconsistent, and the right response to it is silence rather than
    a guess about which half to believe.
    """
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
                "kind": AGREEMENT_KIND,
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
            "kind": AGREEMENT_KIND,
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


async def test_unlinked_architectural_decision_is_not_global(tmp_path, monkeypatch):
    """The noun decides, and a record that names no file is not thereby a rule.

    This is the case the ``source == 'session'`` guess got wrong: a session
    decision accepted through ``confirm --scope`` names files on its acceptance
    row and none on the record, and the guess read that as a repo-wide rule and
    injected it into every session.
    """
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-arch",
                "title": "Keep why free of LLM calls",
                "decision": "the why layer stays deterministic",
                "source": "session",
                "kind": ARCHITECTURAL_KIND,
                "confidence": 0.9,
                "links": [],
            }
        ],
    )
    _quiet_git(monkeypatch)
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_agreement_is_global_whatever_mined_it(tmp_path, monkeypatch):
    """An agreement reaches the agent on its noun, not on its source."""
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-cli-rule",
                "title": "Never use em dashes",
                "decision": "never use em dashes in any output",
                "source": "cli",
                "kind": AGREEMENT_KIND,
                "confidence": 0.8,
                "links": [],
            }
        ],
    )
    _quiet_git(monkeypatch)
    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Never use em dashes" in block


async def test_agreement_that_names_files_is_scored_on_them(tmp_path, monkeypatch):
    """The noun says a record may name nothing, not that its files are noise.

    Mirrors ``crud.authority._is_repo_wide``: both halves are required, so a
    misclassified record that does carry links keeps competing on overlap
    instead of being injected everywhere.
    """
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-linked-agreement",
                "title": "Never use em dashes",
                "decision": "never use em dashes in any output",
                "source": "session",
                "kind": AGREEMENT_KIND,
                "confidence": 0.8,
                "links": [("src/core/auth.py", "file")],
            }
        ],
    )
    _quiet_git(monkeypatch)  # no seeds, so no overlap to score on
    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_pre_split_store_keeps_delivering_its_rules(tmp_path, monkeypatch):
    """A store written before the ``kind`` column still gets its repo-wide rules.

    The hook opens the store read-only and never runs the schema reconciler, so
    it cannot add the column and cannot wait for one. Reading a missing column
    as "no agreements here" would silently stop delivering every rule such a
    store holds, which is the one thing this phase may not do.
    """
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-legacy",
                "title": "Never use em dashes",
                "decision": "never use em dashes in any output",
                "source": "session",
                "kind": AGREEMENT_KIND,
                "confidence": 0.8,
                "links": [],
            }
        ],
    )
    conn = sqlite3.connect(tmp_path / ".repowise" / "wiki.db")
    conn.execute("ALTER TABLE decision_records DROP COLUMN kind")
    conn.commit()
    conn.close()
    _quiet_git(monkeypatch)

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "Never use em dashes" in block


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


async def test_edit_notice_silent_for_an_agreement(tmp_path):
    """The edit-time notice stays keyed on links, and an agreement has none.

    Deliberate, not incidental: an agreement is a claim about how the work is
    conducted, so it has nothing to say about the file being edited, and the
    ``decision_node_links`` join is what keeps it out. Pinned so the delivery
    split does not later grow a second path here.
    """
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "d-agreement",
                "title": "Never use em dashes",
                "decision": "never use em dashes in any output",
                "source": "session",
                "kind": AGREEMENT_KIND,
                "links": [],
            }
        ],
    )
    assert decision_inject._edit_decision_notice(tmp_path, "src/core/auth.py", "s1", {}) is None


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


# ---------------------------------------------------------------------------
# The candidate lane at SessionStart
# ---------------------------------------------------------------------------


_CANDIDATE = {
    "id": "d-cand",
    "title": "Rotate the auth keys nightly",
    "decision": "keys are rotated on a nightly cron",
    "accepted": False,
    "status": "proposed",
    "links": [("src/core/auth.py", "file")],
}


async def test_a_candidate_reaches_the_session_labelled_as_one(tmp_path, monkeypatch):
    """The other half of the contract from the tombstone, and the harder half.

    Before this the hook filtered on acceptance and nothing else, so a store
    with zero acceptance rows injected nothing at all. Restoring candidates as
    an unlabelled second stream would have been worse than the silence.
    """
    await _build_wiki_db(tmp_path, [_CANDIDATE])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")

    assert block is not None
    assert "Rotate the auth keys nightly" in block
    assert "NOT accepted" in block
    assert "Standing decisions" not in block


async def test_an_accepted_decision_is_told_apart_from_a_candidate(tmp_path, monkeypatch):
    """Both reach the agent, in that order, under their own headers."""
    await _build_wiki_db(tmp_path, [_AUTH_DECISION, _CANDIDATE])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")

    assert block is not None
    lines = block.splitlines()
    accepted_at = next(i for i, ln in enumerate(lines) if "Standing decisions" in ln)
    candidate_at = next(i for i, ln in enumerate(lines) if "NOT accepted" in ln)
    jwt_at = next(i for i, ln in enumerate(lines) if "Use JWT auth" in ln)
    rotate_at = next(i for i, ln in enumerate(lines) if "Rotate the auth keys" in ln)
    assert accepted_at < jwt_at < candidate_at < rotate_at


async def test_a_dismissed_candidate_reaches_nobody(tmp_path, monkeypatch):
    """A tombstone carries no acceptance row when it was never accepted, so the
    acceptance test alone reads it as an ordinary candidate."""
    await _build_wiki_db(
        tmp_path,
        [{**_CANDIDATE, "id": "d-tomb", "title": "Tombstoned rule", "status": "dismissed"}],
    )
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    assert decision_inject._session_decision_block(tmp_path, "sess-1") is None


async def test_candidates_cannot_displace_an_accepted_decision(tmp_path, monkeypatch):
    """The budgets are separate, and that is what makes restoring the lane safe.

    The accepted section is selected first under the cap it has always had. A
    shared budget would mean every candidate admitted costs a rule somebody
    actually agreed to.
    """
    long_text = "this decision line pads the token budget " * 8
    accepted = [
        {
            "id": f"d-a{i}",
            "title": f"Accepted number {i}",
            "decision": long_text,
            "links": [("src/core/auth.py", "file")],
        }
        for i in range(8)
    ]
    candidates = [
        {
            "id": f"d-c{i}",
            "title": f"Candidate number {i}",
            "decision": long_text,
            "accepted": False,
            "status": "proposed",
            "links": [("src/core/auth.py", "file")],
        }
        for i in range(8)
    ]
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    alone = tmp_path / "alone"
    await _build_wiki_db(alone, accepted)
    both = tmp_path / "both"
    await _build_wiki_db(both, [*accepted, *candidates])

    block_alone = decision_inject._session_decision_block(alone, "s1")
    block_both = decision_inject._session_decision_block(both, "s2")
    assert block_alone is not None and block_both is not None

    def accepted_lines(block: str) -> list[str]:
        out, seen = [], False
        for ln in block.splitlines():
            if "Standing decisions" in ln:
                seen = True
                continue
            if "NOT accepted" in ln:
                break
            if seen:
                out.append(ln)
        return out

    assert accepted_lines(block_both) == accepted_lines(block_alone)
    assert any("Candidate number" in ln for ln in block_both.splitlines())
    # And the accepted section is still the size it was before candidates
    # existed: eight padded decisions fill it to ``_MAX_ITEMS`` under the
    # unchanged ``_TOKEN_CAP``. Comparing the two runs alone cannot see a
    # budget that shrank for both of them.
    assert len(accepted_lines(block_alone)) == decision_inject._MAX_ITEMS


async def test_the_candidate_lane_has_its_own_caps(tmp_path, monkeypatch):
    long_text = "this candidate line pads the token budget " * 8
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": f"d-c{i}",
                "title": f"Candidate number {i}",
                "decision": long_text,
                "accepted": False,
                "status": "proposed",
                "links": [("src/core/auth.py", "file")],
            }
            for i in range(8)
        ],
    )
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    lines = block.splitlines()
    assert sum("Candidate number" in ln for ln in lines) <= decision_inject._MAX_CANDIDATE_ITEMS
    # Asserted against the accepted cap, not against the candidate one: a test
    # that reads the constant it is pinning passes whatever that constant is
    # set to, which is how a budget merged back into ``_TOKEN_CAP`` would go
    # unnoticed. Half is the loosest reading of "tighter" that still bites.
    assert decision_inject._CANDIDATE_TOKEN_CAP < decision_inject._TOKEN_CAP
    assert decision_inject._estimate_tokens(block) <= decision_inject._TOKEN_CAP // 2


async def test_a_repo_wide_candidate_never_takes_the_whole_lane(tmp_path, monkeypatch):
    """A repo-wide candidate clears the floor on every session by construction,
    so without its own cap the lane would never carry one about the files in
    hand — and on this store there are thirty of them."""
    rules = [
        {
            "id": f"d-g{i}",
            "title": f"Global candidate {i}",
            "decision": f"always follow global candidate number {i}",
            "source": "session",
            "kind": AGREEMENT_KIND,
            "accepted": False,
            "status": "proposed",
            # High enough that a repo-wide rule outscores the linked candidate
            # below (0.5 base vs 0.6 x 0.5 for a seed-file hit). Without that
            # the linked one leads on relevance whatever the cap is, and the
            # cap is not what the test would be measuring.
            "confidence": 1.0,
            "links": [],
        }
        for i in range(5)
    ]
    await _build_wiki_db(tmp_path, [*rules, {**_CANDIDATE, "confidence": 0.5}])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    lines = block.splitlines()
    assert (
        sum("Global candidate" in ln for ln in lines)
        <= decision_inject._MAX_CANDIDATE_GLOBALS
    )
    # The load-bearing half: the lower-scoring linked candidate still gets a
    # slot, which is only true while the globals are held below the item cap.
    assert "Rotate the auth keys nightly" in block


async def test_a_pre_split_store_yields_no_candidates(tmp_path, monkeypatch):
    """Such a store cannot tell a candidate from a decision, so it must not try.

    ``_accepted_clause`` degrades to ``1 = 1`` there, which the candidate query
    negates to nothing. Guessing instead would put the whole review queue of
    every store written before the split in front of an agent.
    """
    await _build_wiki_db(tmp_path, [_AUTH_DECISION, _CANDIDATE])
    conn = sqlite3.connect(tmp_path / ".repowise" / "wiki.db")
    conn.execute("DROP TABLE decision_acceptances")
    conn.commit()
    conn.close()
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    block = decision_inject._session_decision_block(tmp_path, "sess-1")
    assert block is not None
    assert "NOT accepted" not in block
    assert "Rotate the auth keys nightly" not in block


async def test_candidate_injections_are_recorded(tmp_path, monkeypatch):
    """The usage-feedback miner has to be able to ask whether a candidate the
    agent was shown was then followed or contradicted."""
    await _build_wiki_db(tmp_path, [_AUTH_DECISION, _CANDIDATE])
    _quiet_git(monkeypatch, dirty=["src/core/auth.py"])

    assert decision_inject._session_decision_block(tmp_path, "sess-9") is not None

    conn = sqlite3.connect(tmp_path / ".repowise" / "sessions" / "sessions.db")
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM injections").fetchall()}
    conn.close()
    assert ids == {"d-auth", "d-cand"}


async def test_a_candidate_is_never_injected(tmp_path):
    """A record with no acceptance is review material, not guidance.

    The whole point of the entity split lands here: before it, two sightings in
    two transcripts were enough to put a line in front of the agent as though a
    person had agreed to it.
    """
    await _build_wiki_db(
        tmp_path,
        [
            {
                "id": "cand0001",
                "title": "Never accepted by anybody",
                "decision": "do the thing",
                "accepted": False,
                "links": [("src/app.py", "file")],
            }
        ],
    )
    assert decision_inject._edit_decision_notice(tmp_path, "src/app.py", "s1", {}) is None
