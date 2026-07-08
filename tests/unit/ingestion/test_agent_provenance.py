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
    _agent_from_git_ai_note,
)
from repowise.core.ingestion.git_indexer.commit_rows import build_commit_rows
from repowise.core.ingestion.git_indexer.file_history import index_file
from repowise.core.ingestion.git_indexer.records import _CommitRec

CLF = AgentProvenanceClassifier()


def _classify(an="Dev", ae="dev@x.com", cn="Dev", ce="dev@x.com", msg="feat: thing", note_agent=None):
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


def _log_record(sha, an, ae, subj, body="", files=((1, 0, "src/a.py"),), ts=None) -> str:
    ts = ts or int(time.time())
    lines = [f"\x00{sha}\x1f{an}\x1f{ae}\x1f{an}\x1f{ae}\x1f{ts}\x1f\x1f{subj}\x1f{body}"]
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
