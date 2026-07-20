"""Unit tests for deterministic agent-provenance classification.

Covers the channel/tier rules, the precision-first anchoring (service
identities, never bare names), the committer-over-author tier demotion, the
config-driven pattern extension, and the end-to-end flow through the
commit-index walk into commit rows and per-file rollups.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from repowise.core.ingestion.git_commit_index import (
    load_commit_index,
    load_git_ai_note_agents,
)
from repowise.core.ingestion.git_indexer.agent_provenance import (
    AgentProvenanceClassifier,
    AgentTraceIndex,
    _agent_from_git_ai_note,
    _agent_from_trace_record,
    _count_distinct_lines,
    _range_interval,
)
from repowise.core.ingestion.git_indexer.commit_rows import build_commit_rows
from repowise.core.ingestion.git_indexer.file_history import index_file
from repowise.core.ingestion.git_indexer.records import _CommitRec

CLF = AgentProvenanceClassifier()


def _classify(
    an="Dev", ae="dev@x.com", cn="Dev", ce="dev@x.com", msg="feat: thing", note_agent=None
):
    return CLF.classify(an, ae, cn, ce, msg, note_agent=note_agent)


def _git_ai_note(*tools: str) -> str:
    """A minimal git-ai note (v3.0.0): one session per tool, valid metadata."""
    sessions = {
        f"s_{i:014x}": {"agent_id": {"tool": t, "id": f"id{i}", "model": "m"}}
        for i, t in enumerate(tools)
    }
    attestation = "src/a.py\n" + "\n".join(
        f"  s_{i:014x}::t_{i:014x} {i + 1}-{i + 5}" for i in range(len(tools))
    )
    meta = json.dumps(
        {
            "schema_version": "authorship/3.0.0",
            "base_commit_sha": "deadbeef",
            "prompts": {},
            "sessions": sessions,
        }
    )
    return f"{attestation}\n---\n{meta}"


# ---------------------------------------------------------------------------
# Channel / tier rules
# ---------------------------------------------------------------------------


def test_human_commit_is_unlabelled() -> None:
    prov = _classify()
    assert prov.agent is None
    assert prov.autonomy_tier is None


def test_bot_author_email_is_tier1() -> None:
    prov = _classify(
        an="copilot-swe-agent[bot]",
        ae="198982749+Copilot@users.noreply.github.com",
        msg="fix: adjust workflow",
    )
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("copilot", 1, "service_email")
    assert prov.confidence == "high"


def test_service_email_author_is_tier1() -> None:
    prov = _classify(an="Cursor Agent", ae="cursoragent@cursor.com")
    assert (prov.agent, prov.autonomy_tier) == ("cursor", 1)


def test_service_committer_over_human_author_is_tier2_not_tier1() -> None:
    """An agent pushing/amending a human-authored commit is human-driven (T2):
    the committer field alone must never claim full autonomy."""
    prov = _classify(cn="Cursor Agent", ce="cursoragent@cursor.com")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("cursor", 2, "service_committer")


def test_claude_footer_is_tier2() -> None:
    msg = "feat: add parser\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 2, "message_footer")


def test_aider_author_name_suffix_is_tier2() -> None:
    prov = _classify(an="Jane Doe (aider)")
    assert (prov.agent, prov.autonomy_tier) == ("aider", 2)


def test_coauthor_trailer_is_tier3() -> None:
    msg = "fix: handle null\n\nCo-authored-by: Claude Opus 4.5 <noreply@anthropic.com>"
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 3, "coauthor_trailer")


def test_coauthor_trailer_with_bot_noreply_email_is_tier3() -> None:
    """A co-author trailer whose e-mail is a known bot noreply identity is
    matched through the identity registry — no explicit pattern needed."""
    msg = (
        "chore: bump deps\n\n"
        "Co-authored-by: gemini-code-assist[bot] "
        "<176961590+gemini-code-assist[bot]@users.noreply.github.com>"
    )
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("gemini", 3, "coauthor_trailer")


def test_coauthor_trailer_with_vendor_domain_email_is_tier3() -> None:
    msg = "fix: retry\n\nCo-authored-by: Codex <codex@openai.com>"
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("codex", 3, "coauthor_trailer")


def test_agent_local_part_at_vendor_domain_author_is_tier1() -> None:
    prov = _classify(an="Claude", ae="claude@anthropic.com")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 1, "service_email")
    prov = _classify(an="Claude", ae="claude-sonnet@anthropic.com")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 1, "service_email")


def test_vendor_domain_committer_over_human_author_is_tier2() -> None:
    prov = _classify(cn="Claude", ce="claude@anthropic.com")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 2, "service_committer")


def test_human_at_vendor_domain_is_unlabelled() -> None:
    """The vendor domain alone must never label a commit — the local part has
    to name the agent."""
    prov = _classify(an="Jane Doe", ae="jane@anthropic.com")
    assert prov.agent is None
    msg = "fix: typo\n\nCo-authored-by: Jane Doe <jane@anthropic.com>"
    assert _classify(msg=msg).agent is None


def test_tier_precedence_footer_beats_trailer() -> None:
    """A commit carrying both a T2 footer and a T3 trailer is T2 — first match
    in tier order wins."""
    msg = (
        "feat: thing\n\nGenerated with Claude Code\nCo-authored-by: Claude <noreply@anthropic.com>"
    )
    prov = _classify(msg=msg)
    assert prov.autonomy_tier == 2


# ---------------------------------------------------------------------------
# Precision-first anchoring (the false-positive modes that must stay dead)
# ---------------------------------------------------------------------------


def test_human_named_devin_is_not_the_devin_agent() -> None:
    """Co-author patterns are anchored to service e-mails — a human
    contributor named Devin must never match."""
    msg = "feat: routes\n\nCo-authored-by: Devin Ivy <devin@example.com>"
    prov = _classify(msg=msg)
    assert prov.agent is None


def test_mentioning_claude_in_prose_is_not_a_footer() -> None:
    prov = _classify(msg="docs: explain how we generated docs with an LLM, not Claude Code")
    assert prov.agent is None


def test_vendor_domain_lookalike_locals_are_unlabelled() -> None:
    """The vendor-domain channel is an exact allowlist on the local part —
    a human at an agent vendor whose address merely CONTAINS an agent token
    must never classify as an agent."""
    for email in (
        "jean-claude@anthropic.com",
        "claudette@anthropic.com",
        "somecursorfan@cursor.com",
        "jules@openai.com",
    ):
        assert _classify(an="Someone", ae=email).agent is None, email


def test_vendor_domain_allowlists_are_per_vendor_not_a_flat_set() -> None:
    """codex is OpenAI's agent identity, not Anthropic's."""
    assert _classify(an="X", ae="codex@anthropic.com").agent is None


def test_coauthor_human_name_containing_agent_token_is_unlabelled() -> None:
    msg = "feat: sonata\n\nCo-authored-by: Claude Debussy <claude.debussy@anthropic.com>"
    assert _classify(msg=msg).agent is None


def test_mixed_case_coauthor_trailer_matches() -> None:
    msg = "fix: z\n\nCO-AUTHORED-BY: Claude <NoReply@Anthropic.com>"
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 3, "coauthor_trailer")


def test_multi_coauthor_human_first_agent_second_is_detected() -> None:
    msg = (
        "feat: pairing\n\n"
        "Co-authored-by: Jane Doe <jane@example.com>\n"
        "Co-authored-by: Claude <noreply@anthropic.com>"
    )
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier) == ("claude", 3)


def test_devin_service_trailer_matches() -> None:
    msg = (
        "feat: connector\n\n"
        "Co-authored-by: devin-ai-integration[bot] "
        "<158243242+devin-ai-integration[bot]@users.noreply.github.com>"
    )
    prov = _classify(msg=msg)
    assert (prov.agent, prov.autonomy_tier) == ("devin", 3)


# ---------------------------------------------------------------------------
# Assisted-by / Generated-by AI-attribution trailers
# ---------------------------------------------------------------------------


def test_generated_by_trailer_is_tier2() -> None:
    prov = _classify(msg="feat: x\n\nGenerated-by: Claude")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("claude", 2, "generated_by_trailer")


def test_assisted_by_trailer_is_tier3() -> None:
    prov = _classify(msg="feat: x\n\nAssisted-by: GitHub Copilot")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("copilot", 3, "assisted_by_trailer")


def test_assisted_by_chatgpt_normalizes() -> None:
    assert _classify(msg="fix: y\n\nAssisted-by: ChatGPTv5").agent == "chatgpt"


def test_assisted_by_bare_human_name_is_not_an_agent() -> None:
    """The trailer key is an AI marker, but an unrecognized value must not mint
    a phantom agent — a project misusing Assisted-by for a human stays human."""
    prov = _classify(msg="feat: pairing\n\nAssisted-by: Jane Smith")
    assert prov.agent is None


def test_generated_by_beats_assisted_by_on_tier() -> None:
    """A commit with both trailers is classified at the stronger T2."""
    msg = "feat: x\n\nGenerated-by: Cursor\nAssisted-by: Claude"
    assert _classify(msg=msg).autonomy_tier == 2


# ---------------------------------------------------------------------------
# Cursor commit-local signals
# ---------------------------------------------------------------------------


def test_cursor_html_marker_is_detected() -> None:
    assert _classify(msg="feat: x\n\n<!-- Cursor -->").agent == "cursor"


def test_generated_with_cursor_footer_is_detected() -> None:
    assert _classify(msg="feat: x\n\nGenerated with Cursor").agent == "cursor"


# ---------------------------------------------------------------------------
# git-ai authorship notes (refs/notes/ai)
# ---------------------------------------------------------------------------


def test_git_ai_note_single_tool() -> None:
    assert _agent_from_git_ai_note(_git_ai_note("cursor")) == "cursor"


def test_git_ai_note_tool_alias_maps_to_label() -> None:
    assert _agent_from_git_ai_note(_git_ai_note("claude-code")) == "claude"


def test_git_ai_note_dominant_tool_wins() -> None:
    assert _agent_from_git_ai_note(_git_ai_note("cursor", "cursor", "claude")) == "cursor"


def test_git_ai_note_tie_is_ambiguous() -> None:
    assert _agent_from_git_ai_note(_git_ai_note("cursor", "claude")) is None


def test_git_ai_note_malformed_is_no_signal() -> None:
    assert _agent_from_git_ai_note("") is None
    assert _agent_from_git_ai_note("no divider here") is None
    assert _agent_from_git_ai_note("attestation\n---\nnot json") is None


def test_git_ai_note_classified_tier2() -> None:
    prov = _classify(note_agent="cursor")
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("cursor", 2, "git_ai_note")


def test_bot_author_beats_git_ai_note() -> None:
    """T1 service-account authorship outranks a note (more autonomous)."""
    prov = _classify(
        an="Cursor Agent",
        ae="cursoragent@cursor.com",
        note_agent="claude",
    )
    assert (prov.agent, prov.autonomy_tier) == ("cursor", 1)


def test_load_git_ai_note_agents_absent_ref_is_empty_no_log() -> None:
    """The common path: ref absent → {} without ever spawning the notes walk."""
    repo = MagicMock()
    repo.git.for_each_ref.return_value = ""
    assert load_git_ai_note_agents(repo, 100) == {}
    repo.git.log.assert_not_called()


def test_load_git_ai_note_agents_parses_present_ref() -> None:
    repo = MagicMock()
    repo.git.for_each_ref.return_value = "abc123 commit\trefs/notes/ai"
    note = _git_ai_note("cursor")
    repo.git.log.return_value = f"\x00sha_agent\x1f{note}\n\x00sha_human\x1f\n"
    agents = load_git_ai_note_agents(repo, 100)
    assert agents == {"sha_agent": "cursor"}


# ---------------------------------------------------------------------------
# Config-driven extension
# ---------------------------------------------------------------------------


def test_extra_patterns_extend_the_registry() -> None:
    clf = AgentProvenanceClassifier(
        extra_service_emails={"mybot@example.com": ("mybot", 1)},
        extra_footer_patterns=[(r"generated by mybot", "mybot")],
    )
    assert clf.classify("MyBot", "mybot@example.com", "", "", "feat: x").agent == "mybot"
    assert clf.classify("Dev", "d@x.com", "", "", "fix: y\n\nGenerated by MyBot").autonomy_tier == 2
    # Built-ins still work — extras are additive, not a replacement.
    assert (
        clf.classify("Dev", "d@x.com", "", "", "x\n\nGenerated with Claude Code").agent == "claude"
    )


# ---------------------------------------------------------------------------
# End-to-end: walk → sink/bucket → commit rows → per-file rollup
# ---------------------------------------------------------------------------


def _log_record(
    sha, an, ae, subj, body="", files=((1, 0, "src/a.py"),), ts=None, parents=""
) -> str:
    ts = ts or int(time.time())
    lines = [f"\x00{sha}\x1f{an}\x1f{ae}\x1f{an}\x1f{ae}\x1f{ts}\x1f{parents}\x1f{subj}\x1f{body}"]
    for a, d, p in files:
        lines.append(f"{a}\t{d}\t{p}")
    return "\n".join(lines)


def test_walk_carries_provenance_into_sink_rows_and_rollup() -> None:
    raw = "\n".join(
        [
            _log_record(
                "aaa",
                "Dev",
                "dev@x.com",
                "feat: human work",
            ),
            _log_record(
                "bbb",
                "Dev",
                "dev@x.com",
                "fix: agent work",
                body="Generated with Claude Code",
            ),
            _log_record(
                "ccc",
                "copilot-swe-agent[bot]",
                "12345+copilot-swe-agent[bot]@users.noreply.github.com",
                "fix: bot work",
            ),
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    sink: list[dict] = []
    index = load_commit_index(repo, 100, {"src/a.py"}, commit_sink=sink)

    by_sha = {c["sha"]: c for c in sink}
    assert by_sha["aaa"]["agent_name"] is None
    assert by_sha["bbb"]["agent_name"] == "claude"
    assert by_sha["bbb"]["agent_autonomy_tier"] == 2
    assert by_sha["ccc"]["agent_name"] == "copilot"
    assert by_sha["ccc"]["agent_autonomy_tier"] == 1

    # Commit rows carry the provenance columns straight through.
    rows = {r["sha"]: r for r in build_commit_rows(sink)}
    assert rows["aaa"]["agent_name"] is None
    assert rows["bbb"]["agent_name"] == "claude"
    assert rows["bbb"]["agent_channel"] == "message_footer"
    assert rows["ccc"]["agent_autonomy_tier"] == 1
    assert rows["ccc"]["agent_confidence"] == "high"

    # Per-file rollup: 2 of 3 commits agent-attributed, tiers {1: 1, 2: 1}.
    meta = index_file(
        repo,
        "src/a.py",
        repo_path=MagicMock(),
        commit_limit=100,
        follow_renames=False,
        include_blame=False,
        precomputed_commits=index["src/a.py"],
    )
    assert meta["agent_commit_count"] == 2
    assert abs(meta["agent_authored_pct"] - 2 / 3) < 1e-9
    assert json.loads(meta["agent_tier_counts_json"]) == {"1": 1, "2": 1}


def test_rollup_zero_for_human_only_history() -> None:
    commits = [
        _CommitRec(
            sha=f"s{i}",
            author_name="Dev",
            author_email="dev@x.com",
            ts=int(time.time()) - i,
            is_merge=False,
            subject=f"feat: {i}",
        )
        for i in range(3)
    ]
    meta = index_file(
        MagicMock(),
        "src/a.py",
        repo_path=MagicMock(),
        commit_limit=100,
        follow_renames=False,
        include_blame=False,
        precomputed_commits=commits,
    )
    assert meta["agent_commit_count"] == 0
    assert meta["agent_authored_pct"] == 0.0
    assert json.loads(meta["agent_tier_counts_json"]) == {}


# ---------------------------------------------------------------------------
# Agent-trace channel (.agent-trace/traces.jsonl)
# ---------------------------------------------------------------------------


def _trace_record(
    revision, tool="cursor", files=("src/a.py",), contributor_type="ai", model_id=None
) -> str:
    contributor = {"type": contributor_type}
    if model_id:
        contributor["model_id"] = model_id
    return json.dumps(
        {
            "version": "0.1.0",
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "timestamp": "2026-01-25T10:00:00Z",
            "vcs": {"type": "git", "revision": revision},
            "tool": {"name": tool} if tool else None,
            "files": [
                {
                    "path": p,
                    "conversations": [
                        {"contributor": contributor, "ranges": [{"start_line": 1, "end_line": 5}]}
                    ],
                }
                for p in files
            ],
        }
    )


def test_trace_record_resolves_tool_name_first() -> None:
    rec = json.loads(_trace_record("abc", tool="Cursor", model_id="anthropic/claude-opus-4-5"))
    # tool.name resolves the agent; the raw model_id rides along for the split.
    assert _agent_from_trace_record(rec) == (
        "abc",
        "cursor",
        frozenset({"src/a.py"}),
        "anthropic/claude-opus-4-5",
    )


def test_trace_record_falls_back_to_model_id() -> None:
    rec = json.loads(_trace_record("abc", tool=None, model_id="anthropic/claude-opus-4-5"))
    assert _agent_from_trace_record(rec) == (
        "abc",
        "claude",
        frozenset({"src/a.py"}),
        "anthropic/claude-opus-4-5",
    )


def test_trace_record_without_model_id_has_no_model() -> None:
    rec = json.loads(_trace_record("abc", tool="cursor", model_id=None))
    assert _agent_from_trace_record(rec) == ("abc", "cursor", frozenset({"src/a.py"}), None)


def test_trace_record_human_only_is_not_a_signal() -> None:
    rec = json.loads(_trace_record("abc", contributor_type="human"))
    assert _agent_from_trace_record(rec) is None


def test_trace_record_mixed_counts_as_agent() -> None:
    rec = json.loads(_trace_record("abc", contributor_type="mixed"))
    assert _agent_from_trace_record(rec) is not None


def test_trace_record_without_revision_is_skipped() -> None:
    rec = json.loads(_trace_record("abc"))
    del rec["vcs"]
    assert _agent_from_trace_record(rec) is None


def test_trace_index_resolves_exact_and_parent_matches() -> None:
    idx = AgentTraceIndex(
        [
            ("sha_exact", "cursor", frozenset({"src/a.py"}), "anthropic/claude-opus-4-5"),
            ("sha_parent", "claude", frozenset({"src/b.py"}), None),
        ]
    )
    # Revision IS the commit: high confidence, model rides through.
    assert idx.resolve("sha_exact", (), {"src/a.py"}) == (
        "cursor",
        "high",
        "anthropic/claude-opus-4-5",
    )
    # Revision is the commit's parent (trace captured pre-commit): medium.
    assert idx.resolve("sha_child", ("sha_parent",), {"src/b.py"}) == ("claude", "medium", None)
    # A revision match without file overlap must not attribute the commit.
    assert idx.resolve("sha_exact", (), {"other.py"}) is None
    assert idx.resolve("sha_unknown", ("sha_unrelated",), {"src/a.py"}) is None


def test_trace_index_agent_tie_is_ambiguous() -> None:
    idx = AgentTraceIndex(
        [
            ("rev", "cursor", frozenset({"src/a.py"}), None),
            ("rev", "claude", frozenset({"src/b.py"}), None),
        ]
    )
    assert idx.resolve("rev", (), {"src/a.py", "src/b.py"}) is None
    # An unambiguous overlap still resolves.
    assert idx.resolve("rev", (), {"src/a.py"}) == ("cursor", "high", None)


def test_trace_agent_precedence_in_classify() -> None:
    # Trace beats the message-derived channels...
    prov = CLF.classify(
        "Dev",
        "dev@x.com",
        "",
        "",
        "x\n\nGenerated with Claude Code",
        trace_agent="cursor",
        trace_confidence="medium",
    )
    assert (prov.agent, prov.autonomy_tier, prov.channel) == ("cursor", 2, "agent_trace")
    assert prov.confidence == "medium"
    # ...but loses to the commit-attached git-ai note and to a T1 identity.
    prov = CLF.classify("Dev", "dev@x.com", "", "", "x", note_agent="claude", trace_agent="cursor")
    assert prov.channel == "git_ai_note"
    prov = CLF.classify("Cursor Agent", "cursoragent@cursor.com", "", "", "x", trace_agent="claude")
    assert prov.channel == "service_email"


def test_trace_index_load_missing_file_is_empty(tmp_path) -> None:
    repo = MagicMock()
    repo.working_tree_dir = str(tmp_path)
    assert not AgentTraceIndex.load(repo)


def test_trace_index_load_skips_malformed_lines(tmp_path) -> None:
    trace_dir = tmp_path / ".agent-trace"
    trace_dir.mkdir()
    (trace_dir / "traces.jsonl").write_text(
        "not json\n" + _trace_record("rev_a") + "\n{}\n", encoding="utf-8"
    )
    idx = AgentTraceIndex.load(MagicMock(working_tree_dir=str(tmp_path)))
    assert idx.resolve("rev_a", (), {"src/a.py"}) == ("cursor", "high", None)


def test_trace_index_load_never_raises_on_mock_repo() -> None:
    # The walks pass whatever repo object they hold; a non-path working tree
    # (or a bare repo's None) must degrade to an empty index, not an error.
    assert not AgentTraceIndex.load(MagicMock())
    assert not AgentTraceIndex.load(MagicMock(working_tree_dir=None))


def test_walk_attributes_commits_from_trace_file(tmp_path) -> None:
    trace_dir = tmp_path / ".agent-trace"
    trace_dir.mkdir()
    (trace_dir / "traces.jsonl").write_text(
        _trace_record("bbb", tool="cursor")  # captured at commit bbb itself
        + "\n"
        + _trace_record("aaa", tool="opencode", files=("src/b.py",))  # pre-commit HEAD
        + "\n",
        encoding="utf-8",
    )
    raw = "\n".join(
        [
            _log_record("aaa", "Dev", "dev@x.com", "feat: base"),
            _log_record("bbb", "Dev", "dev@x.com", "feat: traced", parents="aaa"),
            # Child of aaa touching src/b.py: attributed via the parent match.
            _log_record(
                "ccc", "Dev", "dev@x.com", "feat: edit", files=((2, 0, "src/b.py"),), parents="aaa"
            ),
        ]
    )
    repo = MagicMock()
    repo.working_tree_dir = str(tmp_path)
    repo.git.for_each_ref.return_value = ""
    repo.git.log.return_value = raw

    sink: list[dict] = []
    load_commit_index(repo, 100, {"src/a.py", "src/b.py"}, commit_sink=sink)
    by_sha = {c["sha"]: c for c in sink}

    assert by_sha["aaa"]["agent_name"] is None
    assert by_sha["bbb"]["agent_name"] == "cursor"
    assert by_sha["bbb"]["agent_channel"] == "agent_trace"
    assert by_sha["bbb"]["agent_confidence"] == "high"
    assert by_sha["ccc"]["agent_name"] == "opencode"
    assert by_sha["ccc"]["agent_confidence"] == "medium"


# ---------------------------------------------------------------------------
# Line-level share + model id
# ---------------------------------------------------------------------------


def _trace_line_record(revision=None, files=(("src/a.py", "ai", None, ((1, 5),)),)) -> str:
    """A trace record with explicit (path, contributor_type, model_id, ranges).

    *ranges* is a tuple of ``(start_line, end_line)`` pairs. *revision* may be
    None to exercise the revision-independent line-share path.
    """
    rec: dict = {
        "version": "0.1.0",
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "timestamp": "2026-01-25T10:00:00Z",
        "files": [
            {
                "path": path,
                "conversations": [
                    {
                        "contributor": (
                            {"type": ctype, "model_id": model} if model else {"type": ctype}
                        ),
                        "ranges": [{"start_line": s, "end_line": e} for s, e in ranges],
                    }
                ],
            }
            for path, ctype, model, ranges in files
        ],
    }
    if revision is not None:
        rec["vcs"] = {"type": "git", "revision": revision}
    return json.dumps(rec)


def _load_traces(tmp_path, *lines: str) -> AgentTraceIndex:
    trace_dir = tmp_path / ".agent-trace"
    trace_dir.mkdir()
    (trace_dir / "traces.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return AgentTraceIndex.load(MagicMock(working_tree_dir=str(tmp_path)))


def test_range_interval_accepts_both_key_styles() -> None:
    assert _range_interval({"start_line": 3, "end_line": 7}) == (3, 7)
    assert _range_interval({"start": 3, "end": 7}) == (3, 7)
    # Malformed / inverted / non-positive spans are dropped, never raise.
    assert _range_interval({"start_line": 5, "end_line": 2}) is None
    assert _range_interval({"start_line": 0, "end_line": 4}) is None
    assert _range_interval({"start_line": "x", "end_line": 4}) is None
    assert _range_interval("nope") is None


def test_count_distinct_lines_unions_overlaps() -> None:
    assert _count_distinct_lines([]) == 0
    assert _count_distinct_lines([(1, 5)]) == 5
    # Overlapping and adjacent runs collapse; disjoint runs add up.
    assert _count_distinct_lines([(1, 5), (4, 8)]) == 8
    assert _count_distinct_lines([(1, 3), (4, 6)]) == 6  # adjacent → one run
    assert _count_distinct_lines([(1, 3), (10, 12)]) == 6  # disjoint


def test_line_shares_counts_distinct_ai_lines(tmp_path) -> None:
    idx = _load_traces(
        tmp_path,
        _trace_line_record("rev1", files=(("src/a.py", "ai", None, ((1, 10),)),)),
    )
    assert idx.line_shares()["src/a.py"] == (10, {})


def test_line_shares_dedupe_overlapping_across_records(tmp_path) -> None:
    # Two records touch overlapping ranges of the same file: the distinct-line
    # union (1-8), not the naive sum (14), is the AI line count.
    idx = _load_traces(
        tmp_path,
        _trace_line_record("rev1", files=(("src/a.py", "ai", None, ((1, 5),)),)),
        _trace_line_record("rev2", files=(("src/a.py", "ai", None, ((4, 8),)),)),
    )
    assert idx.line_shares()["src/a.py"][0] == 8


def test_line_shares_model_breakdown(tmp_path) -> None:
    idx = _load_traces(
        tmp_path,
        _trace_line_record(
            "rev1",
            files=(
                ("src/a.py", "ai", "anthropic/claude-opus-4", ((1, 10),)),
                ("src/a.py", "ai", "anthropic/claude-sonnet-4", ((20, 24),)),
            ),
        ),
    )
    total, models = idx.line_shares()["src/a.py"]
    assert total == 15  # 10 + 5 distinct lines
    assert models == {"anthropic/claude-opus-4": 10, "anthropic/claude-sonnet-4": 5}


def test_line_shares_excludes_human_contributions(tmp_path) -> None:
    idx = _load_traces(
        tmp_path,
        _trace_line_record(
            "rev1",
            files=(
                ("src/a.py", "ai", None, ((1, 4),)),
                ("src/human.py", "human", None, ((1, 100),)),
            ),
        ),
    )
    shares = idx.line_shares()
    assert shares["src/a.py"][0] == 4
    assert "src/human.py" not in shares


def test_line_shares_survive_missing_revision(tmp_path) -> None:
    # A record with no vcs.revision still contributes line share (the rename
    # walk relies on this — it never resolves commits but must count lines).
    idx = _load_traces(
        tmp_path,
        _trace_line_record(None, files=(("src/a.py", "ai", None, ((1, 6),)),)),
    )
    assert not idx._by_revision  # nothing to attribute to a commit
    assert idx.line_shares()["src/a.py"] == (6, {})
    assert bool(idx)  # line share alone makes the index truthy


def test_trace_record_dominant_model_wins(tmp_path) -> None:
    # One agent, two models across three files: the majority model is the
    # commit-level model_id; a tie would resolve to None.
    rec = json.loads(
        _trace_line_record(
            "rev1",
            files=(
                ("a.py", "ai", "anthropic/claude-opus-4", ((1, 2),)),
                ("b.py", "ai", "anthropic/claude-opus-4", ((1, 2),)),
                ("c.py", "ai", "anthropic/claude-sonnet-4", ((1, 2),)),
            ),
        )
    )
    parsed = _agent_from_trace_record(rec)
    assert parsed is not None
    _rev, agent, _paths, model_id = parsed
    assert agent == "claude"
    assert model_id == "anthropic/claude-opus-4"


def test_walk_populates_agent_model_id(tmp_path) -> None:
    (tmp_path / ".agent-trace").mkdir()
    (tmp_path / ".agent-trace" / "traces.jsonl").write_text(
        _trace_line_record("bbb", files=(("src/a.py", "ai", "anthropic/claude-opus-4", ((1, 5),)),))
        + "\n",
        encoding="utf-8",
    )
    raw = "\n".join(
        [
            _log_record("bbb", "Dev", "dev@x.com", "feat: traced", files=((3, 0, "src/a.py"),)),
        ]
    )
    repo = MagicMock()
    repo.working_tree_dir = str(tmp_path)
    repo.git.for_each_ref.return_value = ""
    repo.git.log.return_value = raw

    sink: list[dict] = []
    load_commit_index(repo, 100, {"src/a.py"}, commit_sink=sink)
    row = {c["sha"]: c for c in sink}["bbb"]
    assert row["agent_channel"] == "agent_trace"
    assert row["agent_model_id"] == "anthropic/claude-opus-4"


def test_agent_model_id_null_when_trace_channel_loses() -> None:
    # A service-identity author (T1) outranks the trace channel, so no model id
    # is carried even when a trace record resolved a model for the commit.
    prov = CLF.classify(
        "Cursor Agent",
        "cursoragent@cursor.com",
        "",
        "",
        "feat: x",
        trace_agent="claude",
        trace_confidence="high",
    )
    assert prov.channel == "service_email"
    # The sink gate keys on channel == "agent_trace"; here it is not, so the
    # model id (available from the trace hit) is deliberately dropped.
    model_id = "anthropic/claude-opus-4" if prov.channel == "agent_trace" else None
    assert model_id is None
