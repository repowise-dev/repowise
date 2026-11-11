"""Path mode leads with what constrains an edit and stops; history is opt-in.

The rules in force come first, then at most two background records, labelled.
Origin story, alignment, archaeology, rationale comments and episodes arrive
only with ``include=["history"]``. Restatements fold into the record they
restate, a background row ranked below a rule that answers better is not
served, and a moved path is answered at its new location.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

_HISTORY_LANES = ("origin_story", "alignment", "episodes", "git_archaeology", "code_rationale")


@pytest.mark.asyncio
async def test_a_path_answer_is_its_constraints_and_a_pointer_to_history(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")

    assert result["mode"] == "path"
    assert not any(lane in result for lane in _HISTORY_LANES)
    assert 'include=["history"]' in result["more"]
    # The fixture's JWT record was never accepted: background, labelled so.
    assert result["decisions"] == []
    assert result["candidates"]
    assert all(row["authority"] == "candidate" for row in result["candidates"])
    assert "not rules" in result["candidates_note"]
    assert result["answer_basis"] == "candidate"


@pytest.mark.asyncio
async def test_a_background_row_is_small(setup_mcp):
    from repowise.server.mcp_server import get_why

    row = (await get_why("src/auth/service.py"))["candidates"][0]

    for detail in ("context", "rationale", "consequences", "alternatives", "affected_files"):
        assert detail not in row
    assert row["decision"] and row["title"]


@pytest.mark.asyncio
async def test_history_is_served_when_asked_for(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py", include=["history"])

    assert result["origin_story"]["available"] is True
    assert "alignment" in result
    assert "more" not in result


@pytest.mark.asyncio
async def test_an_unknown_include_is_named_not_applied(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py", include=["histroy"])

    assert "origin_story" not in result
    assert result["ignored_arguments"][0]["values"] == ["histroy"]


@pytest.mark.asyncio
async def test_several_targets_get_lean_cards(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why(targets=["src/auth/service.py", "src/db/models.py"])

    for card in result["target_context"].values():
        assert "origin" not in card and "git_archaeology" not in card
    assert 'include=["history"]' in result["more"]


@pytest.mark.asyncio
async def test_background_is_capped_at_two(session, setup_mcp):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why.caps import _MAX_BACKGROUND

    await _seed_background(session, setup_mcp, "src/auth/service.py", 5)

    result = await get_why("src/auth/service.py")

    assert len(result["candidates"]) == _MAX_BACKGROUND
    assert result["candidates_total"] > _MAX_BACKGROUND


# --- moved paths --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_renamed_path_is_answered_at_its_new_name(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import path_mode

    monkeypatch.setattr(
        path_mode,
        "missing_path_hint",
        lambda root, path: {"removed_by_commit": "a" * 40, "moved_to": ["src/auth/service.py"]},
    )

    result = await get_why("src/auth/old_service.py")

    assert "error" not in result
    assert result["path"] == "src/auth/service.py"
    assert result["moved_from"] == "src/auth/old_service.py"
    assert result["removed_by_commit"] == "a" * 12


@pytest.mark.asyncio
async def test_a_module_split_into_a_package_is_answered_for_the_package(
    setup_mcp, monkeypatch
):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import path_mode

    monkeypatch.setattr(
        path_mode,
        "missing_path_hint",
        lambda root, path: {
            "removed_by_commit": "b" * 40,
            "moved_to": ["src/auth/a.py", "src/auth/b.py"],
        },
    )

    result = await get_why("src/auth.py")

    assert result["path"] == "src/auth"
    assert result["moved_from"] == "src/auth.py"


@pytest.mark.asyncio
async def test_a_removed_path_names_the_commit_that_removed_it(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import path_mode

    monkeypatch.setattr(
        path_mode,
        "missing_path_hint",
        lambda root, path: {"removed_by_commit": "c" * 40, "moved_to": []},
    )

    result = await get_why("src/gone/old.py")

    assert result["removed_by_commit"] == "c" * 12
    assert "removed" in result["error"]


# --- restatements and the tail ------------------------------------------------


def _rec(id_: str, commit: str, source: str = "pr") -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        source=source,
        status="proposed",
        superseded_by=None,
        evidence_commits_json=json.dumps([commit]),
        evidence_file=None,
        evidence_line=None,
    )


def test_a_background_record_restating_a_rule_folds_into_the_rule():
    from repowise.server.mcp_server.tool_why.path_mode import _collapse_path_records

    full = "d" * 40
    # Ranked background first, as status ordering can leave it.
    background = _rec("mined", full[:8])
    rule = _rec("rule", full[:12])
    currencies = {"rule": "active"}

    served, restates = _collapse_path_records(
        [background, rule], currencies, {full[:8]: full, full[:12]: full}
    )

    assert [d.id for d in served] == ["rule"]
    assert restates == {"rule": ["mined"]}


def test_abbreviated_commits_compare_only_through_git_resolution():
    from repowise.server.mcp_server.tool_why.ranking import _collapse_restatements

    a, b = _rec("a", "e" * 8), _rec("b", "e" * 9)

    assert len(_collapse_restatements([a, b])) == 2
    assert _collapse_restatements([a, b], {"e" * 8: "e" * 40, "e" * 9: "e" * 40}) == [
        (a, ["b"])
    ]


def test_background_ranked_below_a_rule_that_answers_better_is_not_served():
    from repowise.server.mcp_server._budget import OmissionCollector
    from repowise.server.mcp_server.tool_why.search import _behind_an_answer

    rows = [
        {"id": "rule", "authority": "accepted"},
        {"id": "weaker", "authority": "candidate"},
        {"id": "as-good", "authority": "candidate"},
        {"id": "other-rule", "authority": "accepted"},
    ]
    scores = {"rule": 1.0, "weaker": 0.75, "as-good": 1.0, "other-rule": 0.7}

    kept = _behind_an_answer(rows, scores, OmissionCollector("get_why"))

    assert [r["id"] for r in kept] == ["rule", "as-good", "other-rule"]


def test_the_tail_is_untouched_when_no_rule_leads():
    from repowise.server.mcp_server._budget import OmissionCollector
    from repowise.server.mcp_server.tool_why.search import _behind_an_answer

    rows = [{"id": "x", "authority": "candidate"}, {"id": "y", "authority": "candidate"}]

    assert _behind_an_answer(rows, {"x": 1.0, "y": 0.6}, OmissionCollector("get_why")) == rows


async def _seed_background(session, rid: str, path: str, count: int) -> None:
    from repowise.core.persistence.models import DecisionRecord

    for i in range(count):
        session.add(
            DecisionRecord(
                id=f"bg{i}",
                repository_id=rid,
                title=f"Background record {i}",
                status="proposed",
                context="ctx",
                decision=f"decision {i}",
                rationale="why",
                affected_files_json=json.dumps([path]),
                affected_modules_json="[]",
                source="pr",
                evidence_commits_json=json.dumps([f"{i + 1:040x}"]),
                confidence=0.5,
                staleness_score=0.0,
            )
        )
    await session.flush()


def test_a_rule_is_never_folded_under_a_mined_record_ranked_above_it():
    from repowise.server.mcp_server.tool_why.ranking import _collapse_restatements

    full = "f" * 40
    mined, rule, later = _rec("mined", full), _rec("rule", full), _rec("later", full)

    out = _collapse_restatements([mined, rule, later], in_force={"rule"})

    assert [(d.id, folded) for d, folded in out] == [("mined", []), ("rule", ["later"])]


@pytest.mark.asyncio
async def test_a_split_is_followed_only_into_its_own_package(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import path_mode

    monkeypatch.setattr(
        path_mode,
        "missing_path_hint",
        lambda root, path: {"removed_by_commit": "d" * 40, "moved_to": ["x/a.py", "y/b.py"]},
    )

    result = await get_why("src/auth.py")

    assert "moved_from" not in result
    assert result["removed_by_commit"] == "d" * 12


def test_a_lean_path_summary_names_only_what_was_served():
    from repowise.server.chat_summaries import _why_path_summary

    assert _why_path_summary({"decisions": [{"id": "a"}]}) == "1 decision(s)"
    assert _why_path_summary(
        {
            "decisions": [],
            "alignment": {"score": "low"},
            "origin_story": {"available": True, "primary_author": "Alice"},
        }
    ) == "0 decision(s), alignment: low, author: Alice"
