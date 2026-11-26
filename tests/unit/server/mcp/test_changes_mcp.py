"""Change Records and attribution on the MCP surface.

``change:<id>`` targets and ``include=["changes", "provenance"]`` in get_context,
``include=["changes"]`` in get_risk and get_overview. Default responses,
get_why's included, stay byte-identical.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import update

from repowise.core import attribution
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitMetadata
from repowise.core.suspects.index import NOTE

_FULL = {
    "abc1234": "abc1234" + "0" * 33,
    "def5678": "def5678" + "0" * 33,
}  # commits the fixture's get_why archaeology names


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _row(change_id: str, day: int, files: list[str], **over) -> dict:
    sha = over.pop("sha", f"{day:02d}" + "b" * 38)
    number = change_id.removeprefix("pr-")
    row = {
        "change_id": change_id,
        "identity_kind": "pr",
        "identity_value": number,
        "shape": "squash",
        "forge": "github",
        "forge_ref": f"#{number}",
        "title": f"feat: change {number}",
        "merged_at": datetime(2026, 3, day, tzinfo=UTC),
        "head_sha": sha,
        "author_name": "Ann",
        "author_email": "ann@example.com",
        "commit_shas_json": json.dumps([sha]),
        "files_json": json.dumps([{"path": p, "added": 1, "deleted": 0} for p in files]),
        "files_changed": len(files),
        "lines_added": len(files),
        "lines_deleted": 0,
        "category": "feature",
        "provenance": "agent",
        "provenance_agent": 1,
        "provenance_known": 1,
        "metadata_source": "git",
    }
    row.update(over)
    return row


_ROWS = [
    _row("pr-1", 1, ["src/auth/service.py"], sha=_FULL["def5678"], title="Add JWT support"),
    _row(
        "pr-2",
        2,
        ["src/auth/service.py", "src/db/models.py"],
        category="fix",
        title="fix: token refresh",
        provenance="human_attested",
        provenance_agent=0,
    ),
    _row(
        "pr-3",
        3,
        ["src/auth/service.pyc"],  # a longer path never matches src/auth/service.py
        provenance="unknown",
        provenance_agent=0,
        provenance_known=0,
    ),
    _row(
        "pr-4",
        15,
        ["src/auth/service.py"],
        sha=_FULL["abc1234"],
        title="Merge pull request #4 from ann/auth",
        provenance=None,
        provenance_agent=None,
        provenance_known=None,
    ),
]


async def _seed(session, rows=_ROWS) -> None:
    await crud.upsert_changes(session, "repo1", rows)
    await session.commit()


def _without_meta(payload: dict) -> str:
    return json.dumps({k: v for k, v in payload.items() if k != "_meta"}, sort_keys=True)


def _attribution_off(root: Path) -> None:
    (root / ".repowise").mkdir(exist_ok=True)
    (root / ".repowise" / "config.yaml").write_text(
        "attribution:\n  enabled: false\n", encoding="utf-8"
    )


# -- get_context ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_target_card(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    result = await get_context(
        targets=["change:pr-2", "change:def5678", "change:pr-4", "change:pr-99"]
    )
    card = result["targets"]["change:pr-2"]
    assert card["type"] == "change"
    assert (card["id"], card["ref"], card["title"]) == ("pr-2", "#2", "fix: token refresh")
    assert card["merged_at"].startswith("2026-03-02")
    assert card["category"] == "fix"
    assert card["files"] == ["src/auth/service.py", "src/db/models.py"]
    assert card["provenance"] == {
        "label": "human_attested",
        "agent_commits": 0,
        "human_attested_commits": 1,
        "unknown_commits": 0,
        "coverage": 1.0,
    }
    # A commit sha resolves to the change that landed it.
    assert result["targets"]["change:def5678"]["id"] == "pr-1"
    assert result["targets"]["change:pr-4"]["provenance"] == {"unavailable": "not_recorded"}
    miss = result["targets"]["change:pr-99"]["error"]
    assert "No change 'pr-99'" in miss and "change-I" in miss and "MR" in miss
    assert "Ann" not in json.dumps(card)


@pytest.mark.asyncio
async def test_change_card_withholds_provenance_when_attribution_is_off(
    setup_mcp, session, tmp_path
):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    _attribution_off(tmp_path)
    card = (await get_context(targets=["change:pr-1"]))["targets"]["change:pr-1"]
    assert card["provenance"] == {"unavailable": "attribution_off"}


@pytest.mark.asyncio
async def test_file_card_lists_recent_changes_on_request(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session)
    card = (await get_context(targets=["src/auth/service.py"], include=["changes"]))["targets"][
        "src/auth/service.py"
    ]
    rows = card["recent_changes"]
    assert [r["id"] for r in rows] == ["pr-4", "pr-2", "pr-1"]  # never pr-3's .pyc
    assert rows[1] == {
        "id": "pr-2",
        "ref": "#2",
        "title": "fix: token refresh",
        "merged_at": rows[1]["merged_at"],
        "category": "fix",
        "provenance": {"label": "human_attested", "coverage": 1.0},
    }
    assert "provenance" not in rows[0]  # stored without a label
    assert "recent_changes_total" not in card


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def _symbol_history(session, root: Path) -> dict[str, str]:
    """A checkout where one commit edits ``login`` (lines 20-40) and two do not."""
    from repowise.core.persistence.models import Repository

    path = root / "src/auth/service.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"x{n} = {n}" for n in range(1, 121)]
    _git(root, "init", "-q")
    shas = {}
    for name, line in (("seed", None), ("outside", 4), ("inside", 30), ("after", 110)):
        if line is not None:
            lines[line - 1] = f"x{line} = '{name}'"
        path.write_text("".join(f"{text}\n" for text in lines), encoding="utf-8")
        _git(root, "add", "src")
        _git(root, "commit", "-qm", name)
        shas[name] = _git(root, "rev-parse", "HEAD")
    await session.execute(
        update(Repository).where(Repository.id == "repo1").values(head_commit=shas["after"])
    )
    file = ["src/auth/service.py"]
    await _seed(
        session,
        [
            _row("pr-10", 10, file, sha=shas["outside"]),
            _row("pr-11", 11, file, sha=shas["inside"], category="fix", title="fix: login"),
            _row("pr-12", 12, file, sha=shas["after"]),
        ],
    )
    return shas


@pytest.mark.asyncio
async def test_symbol_card_lists_only_the_changes_that_edited_its_lines(
    setup_mcp, session, tmp_path, monkeypatch
):
    from repowise.server.mcp_server import _changes, get_context

    await _symbol_history(session, tmp_path)
    # The file scan's cap would keep only pr-12; the symbol's edit is older.
    monkeypatch.setattr(_changes, "_FILE_SCAN_CAP", 1)
    target = "src/auth/service.py::login"
    card = (await get_context(targets=[target], include=["changes"]))["targets"][target]
    assert card["changes_scope"] == "symbol"
    assert [(r["id"], r["category"]) for r in card["recent_changes"]] == [("pr-11", "fix")]
    # The file-level count stays, labelled as the file's.
    assert card.get("fix_history", {}).get("scope", "file") == "file"


@pytest.mark.asyncio
async def test_symbol_last_change_is_the_newest_commit_on_its_lines(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    shas = await _symbol_history(session, tmp_path)
    target, path = "src/auth/service.py::login", "src/auth/service.py"
    named = await get_context(targets=[target, path], include=["last_change"])
    own = named["targets"][target]["last_change"]
    committed = _git(tmp_path, "show", "-s", "--format=%cI", shas["inside"])
    # The method's own last fix and its author, not the file's newer "after" commit.
    assert own["author"] == "Dev" and own["ref"] == "#11" and "scope" not in own
    # Dated by the stored change that landed it, with the day it was written.
    assert own["date"].startswith("2026-03-11")
    assert own["authored"] == committed[:10]
    assert "ref" not in named["targets"][path]["last_change"]
    # Opt-in: a default card runs no walk and carries no last_change.
    assert "last_change" not in (await get_context(targets=[target]))["targets"][target]


@pytest.mark.asyncio
async def test_symbol_last_change_falls_back_to_the_file_labelled(setup_mcp, session):
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server import get_context

    await session.execute(
        update(Repository).where(Repository.id == "repo1").values(head_commit=None)
    )
    await session.commit()
    target, path = "src/auth/service.py::login", "src/auth/service.py"
    cards = (await get_context(targets=[target, path], include=["last_change"]))["targets"]
    assert cards[target]["last_change"] == {**cards[path]["last_change"], "scope": "file"}


@pytest.mark.asyncio
async def test_symbol_changes_say_file_level_when_git_cannot_walk_its_lines(
    setup_mcp, session, tmp_path
):
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server import get_context

    await _seed(session)
    target = "src/auth/service.py::login"

    async def scope() -> tuple[str, str]:
        card = (await get_context(targets=[target], include=["changes"]))["targets"][target]
        assert [r["id"] for r in card["recent_changes"]] == ["pr-4", "pr-2", "pr-1"]
        return card["changes_scope"], card["changes_scope_reason"]

    repo = update(Repository).where(Repository.id == "repo1")
    await session.execute(repo.values(head_commit=None))
    await session.commit()
    assert await scope() == ("file", "no_indexed_commit")
    # An indexed commit but no checkout under the repo root: git has no history to walk.
    await session.execute(repo.values(head_commit="f" * 40))
    await session.commit()
    assert await scope() == ("file", "git_history_unavailable")


@pytest.mark.asyncio
async def test_symbol_card_labels_file_fix_history(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(prior_defect_count=3, last_fix_at=datetime.now(UTC))
    )
    await session.commit()
    result = await get_context(targets=["src/auth/service.py::login", "src/auth/service.py"])
    symbol = result["targets"]["src/auth/service.py::login"]["fix_history"]
    file = result["targets"]["src/auth/service.py"]["fix_history"]
    assert symbol == {**file, "scope": "file"}
    assert "scope" not in file


@pytest.mark.asyncio
async def test_change_rows_below_the_coverage_floor_carry_no_provenance(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    path = "src/auth/service.py"
    shas = [f"{n:02d}" + "c" * 38 for n in range(4)]
    # Known commits over all: 0 of 1, 1 of 4 (25%) and 1 of 3 (33%) against a 30% floor.
    await _seed(
        session,
        [
            _row("pr-7", 7, [path], provenance="unknown", provenance_known=0),
            _row("pr-8", 8, [path], commit_shas_json=json.dumps(shas), provenance_known=1),
            _row("pr-9", 9, [path], commit_shas_json=json.dumps(shas[:3]), provenance_known=1),
        ],
    )
    card = (await get_context(targets=[path], include=["changes"]))["targets"][path]
    rows = {r["id"]: r for r in card["recent_changes"]}
    assert "provenance" not in rows["pr-7"] and "provenance" not in rows["pr-8"]
    assert rows["pr-9"]["provenance"] == {"label": "agent", "coverage": 0.3333}


@pytest.mark.asyncio
async def test_recent_changes_are_capped_with_their_total(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(session, [_row(f"pr-{n}", n, ["src/db/models.py"]) for n in range(1, 9)])
    card = (await get_context(targets=["src/db/models.py"], include=["changes"]))["targets"][
        "src/db/models.py"
    ]
    assert [r["id"] for r in card["recent_changes"]] == ["pr-8", "pr-7", "pr-6", "pr-5", "pr-4"]
    assert card["recent_changes_total"] == 8


@pytest.mark.asyncio
async def test_suspects_rank_recent_changes_against_the_targets(setup_mcp, session):
    import time_machine

    from repowise.server.mcp_server import get_context

    await _seed(session)
    with time_machine.travel(datetime(2026, 3, 16, tzinfo=UTC), tick=False):
        plain = await get_context(targets=["src/db/models.py"])
        result = await get_context(targets=["src/db/models.py"], include=["suspects"])
    assert "suspects" not in plain
    block = result["suspects"]
    assert block["window"]["since"].startswith("2026-03-02")
    assert block["candidates"] == 3  # pr-2, pr-3, pr-4; pr-1 is older than 14 days
    assert block["candidates_capped"] is False
    top = block["suspects"][0]
    assert (top["id"], top["rank"]) == ("pr-2", 1)
    assert "text" in top["signals"] and "recency" in top["signals"]
    assert block["note"] == NOTE  # a row has evidence beyond recency: nothing to widen
    for row in block["suspects"]:
        assert not {"term py", "term src"} & set(row["signals"].get("text", {}).get("evidence", []))


@pytest.mark.asyncio
async def test_a_typed_symptom_is_the_suspects_text_query(setup_mcp, session):
    import time_machine

    from repowise.server.mcp_server import get_context

    await _seed(session)
    with time_machine.travel(datetime(2026, 3, 16, tzinfo=UTC), tick=False):
        result = await get_context(
            targets=["src/db/models.py"], include=["suspects"], symptom="token refresh fails"
        )
        alone = await get_context(targets=["src/db/models.py"], symptom="token refresh fails")
    top = result["suspects"]["suspects"][0]
    assert top["id"] == "pr-2"
    assert "term refresh" in top["signals"]["text"]["evidence"]
    # A symptom alone asks for suspects; it is never dropped as an ignored argument.
    assert alone["suspects"]["suspects"][0]["id"] == "pr-2"
    assert "ignored_arguments" not in alone


@pytest.mark.asyncio
async def test_file_provenance_carries_coverage_and_withholds_below_the_floor(
    setup_mcp, session, tmp_path
):
    from repowise.server.mcp_server import get_context

    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(agent_commit_count=6, attribution_known_count=21)
    )
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/db/models.py")
        .values(agent_commit_count=1, attribution_known_count=2)
    )
    await session.commit()
    result = await get_context(
        targets=["src/auth/service.py", "src/db/models.py"], include=["provenance"]
    )
    covered = result["targets"]["src/auth/service.py"]["provenance"]
    assert covered["agent_commits"] == 6
    assert covered["human_attested_commits"] == 15
    assert covered["unknown_commits"] == 21
    assert covered["coverage"] == 0.5
    assert covered["agent_share"] == round(6 / 21, 4)
    withheld = result["targets"]["src/db/models.py"]["provenance"]
    assert withheld["agent_share"] is None
    assert withheld["suppressed_reason"] == "coverage_below_floor"
    assert withheld["unknown_commits"] == 13  # unknown is its own band
    # A withheld share carries no counts that would restate it.
    assert "agent_commits" not in withheld and "human_attested_commits" not in withheld

    _attribution_off(tmp_path)
    off = await get_context(targets=["src/auth/service.py"], include=["provenance"])
    assert off["targets"]["src/auth/service.py"]["provenance"] == {"unavailable": "attribution_off"}


def test_change_is_a_target_kind_not_a_repo_alias():
    from repowise.server.mcp_server._workspace_routing import _route_one

    route = _route_one("change:pr-1", {"change": Path("/x")}, "main", False)
    assert (route.repo, route.local, route.routed) == ("main", "change:pr-1", False)


# -- get_risk -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_lists_recent_changes_on_request(setup_mcp, session):
    from repowise.server.mcp_server import get_risk

    await _seed(session)
    result = await get_risk(targets=["src/db/models.py"], include=["changes"])
    card = result["targets"]["src/db/models.py"]
    assert [r["id"] for r in card["recent_changes"]] == ["pr-2"]
    assert "ignored_arguments" not in result


# -- get_overview ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_changes_by_category_week_and_agent_share(setup_mcp, session):
    from repowise.server.mcp_server import get_overview

    await _seed(session)
    block = (await get_overview(include=["changes"]))["changes"]
    assert block["total"] == 4
    assert block["window"]["days"] == 90
    assert block["window"]["until"].startswith("2026-03-15")
    assert block["by_category"] == {"feature": 3, "fix": 1}
    weeks = {w["week"]: w["changes"] for w in block["by_week"]}
    # History starts inside the window: earlier weeks are unknown, not zero.
    assert block["window"]["history_from"].startswith("2026-03-01")
    assert block["by_week"][0]["week"] == "2026-02-23"
    assert weeks["2026-02-23"] == 1 and weeks["2026-03-02"] == 2 and weeks["2026-03-09"] == 1
    assert sum(weeks.values()) == 4
    prov = block["provenance"]
    # Two of four changes have a known source: unknown never counts as human.
    assert (prov["agent_changes"], prov["human_attested_changes"], prov["unknown_changes"]) == (
        1,
        1,
        2,
    )
    assert prov["coverage"] == 0.5 and prov["agent_share"] == 0.5
    assert "covered changes" in prov["summary"]


@pytest.mark.asyncio
async def test_overview_agent_share_is_withheld_below_the_floor(setup_mcp, session):
    from repowise.server.mcp_server import get_overview

    rows = [
        _row(f"pr-{n}", n, ["src/x.py"], provenance="unknown", provenance_known=0)
        for n in range(1, 5)
    ]
    rows.append(_row("pr-5", 5, ["src/x.py"]))
    await _seed(session, rows)
    prov = (await get_overview(include=["changes"]))["changes"]["provenance"]
    assert prov["coverage"] == 0.2
    assert prov["agent_share"] is None
    assert prov["suppressed_reason"] == "coverage_below_floor"
    assert "agent_changes" not in prov and "human_attested_changes" not in prov
    assert prov["unknown_changes"] == 4


@pytest.mark.asyncio
async def test_overview_changes_without_records(setup_mcp):
    from repowise.server.mcp_server import get_overview

    assert (await get_overview(include=["changes"]))["changes"] == {"total": 0}


@pytest.mark.asyncio
async def test_a_short_sha_in_several_changes_lists_candidates(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _seed(
        session,
        [
            _row("pr-1", 1, ["a.py"], sha="cafe123" + "1" * 33),
            _row("pr-2", 2, ["a.py"], sha="cafe123" + "2" * 33),
        ],
    )
    result = await get_context(targets=["change:cafe123", "change:cafe1232"])
    card = result["targets"]["change:cafe123"]
    assert "several changes" in card["error"]
    assert sorted(card["candidates"]) == ["change:pr-1", "change:pr-2"]
    assert result["targets"]["change:cafe1232"]["id"] == "pr-2"


@pytest.mark.asyncio
async def test_recent_changes_match_the_exact_path_only(setup_mcp, session):
    """LIKE wildcards and SQLite's ASCII case folding never widen the match."""
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server._changes import file_changes

    await _seed(
        session,
        [
            _row("pr-1", 1, ["src/a_b.py"]),
            _row("pr-2", 2, ["src/aXb.py"]),
            _row("pr-3", 3, ["src/100%.py"]),
            _row("pr-4", 4, ["src/100x.py"]),
            _row("pr-5", 5, ["src/A_B.py"]),
        ],
    )
    repository = await session.get(Repository, "repo1")
    found = await file_changes(session, repository, ["src/a_b.py", "src/100%.py"], attribution=True)
    ids = {path: [r["id"] for r in block["recent_changes"]] for path, block in found.items()}
    assert ids == {"src/a_b.py": ["pr-1"], "src/100%.py": ["pr-3"]}


@pytest.mark.asyncio
async def test_risk_reads_every_cards_changes_in_one_query(setup_mcp, session, monkeypatch):
    from repowise.server.mcp_server import _changes, get_risk

    await _seed(session)
    calls = []
    original = _changes.file_changes

    async def counted(*args, **kwargs):
        calls.append(args[2])
        return await original(*args, **kwargs)

    module = sys.modules["repowise.server.mcp_server.tool_risk.get_risk"]
    monkeypatch.setattr(module, "file_changes", counted)
    result = await get_risk(
        targets=["src/auth/service.py", "src/db/models.py"], include=["changes"]
    )
    assert len(calls) == 1 and sorted(calls[0]) == ["src/auth/service.py", "src/db/models.py"]
    assert [r["id"] for r in result["targets"]["src/db/models.py"]["recent_changes"]] == ["pr-2"]


# -- defaults never grow ----------------------------------------------------------


@pytest.mark.asyncio
async def test_default_responses_are_byte_identical_with_change_records(setup_mcp, session):
    from repowise.server.mcp_server import get_context, get_overview, get_risk, get_why

    async def defaults() -> list[str]:
        return [
            _without_meta(await get_context(targets=["src/auth/service.py"])),
            _without_meta(await get_risk(targets=["src/auth/service.py"])),
            _without_meta(await get_overview()),
            _without_meta(await get_why(query="src/auth/service.py")),
        ]

    before = await defaults()
    await _seed(session)
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(attribution_known_count=21)
    )
    await session.commit()
    assert await defaults() == before


@pytest.mark.parametrize(
    "out", [None, "", "abc\x1fDev", "abc\x1fDev\x1fnot-a-date\x1f2026-01-18T00:00:00Z"]
)
def test_a_failed_newest_line_walk_is_not_cached(monkeypatch, out):
    from repowise.server.mcp_server import _changes

    key = ("root", "c" * 40, f"x{out!r}.py", (1, 2))
    monkeypatch.setattr(_changes, "_line_log", lambda *a, **k: out)
    with pytest.raises(_changes._LineHistoryError):
        _changes._newest_line_commit(*key)
    # The next call walks again rather than replaying the failure.
    good = "abc\x1fDev\x1f2026-09-29T14:28:33+05:30\x1f2026-01-18T10:00:00+05:30\n"
    monkeypatch.setattr(_changes, "_line_log", lambda *a, **k: good)
    assert _changes._newest_line_commit(*key) == (
        "abc",
        "Dev",
        datetime(2026, 9, 29, 8, 58, 33),
        "2026-01-18",
    )


def _dated(root: Path, when: str, *args: str, authored: str | None = None) -> str:
    """``git *args`` committed at *when*, authored at *authored* (default *when*)."""
    import os
    import subprocess

    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": f"{when}T12:00:00Z",
        "GIT_AUTHOR_DATE": f"{authored or when}T12:00:00Z",
    }
    return subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


@pytest.fixture(params=["merge", "squash", "rebase"])
async def late_landing(request, setup_mcp, session, tmp_path):
    """``login``'s lines edited on a branch on 2026-01-18 that lands on 2026-08-12."""
    from repowise.core.persistence.models import Repository

    path = tmp_path / "src/auth/service.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"x{n} = {n}" for n in range(1, 121)]
    path.write_text("".join(f"{t}\n" for t in lines), encoding="utf-8")
    _dated(tmp_path, "2026-01-01", "init", "-q", "-b", "main")
    _dated(tmp_path, "2026-01-01", "add", "src")
    _dated(tmp_path, "2026-01-01", "commit", "-qm", "seed")
    _dated(tmp_path, "2026-01-18", "checkout", "-qb", "feat")
    lines[29] = "x30 = 'fixed'"
    path.write_text("".join(f"{t}\n" for t in lines), encoding="utf-8")
    _dated(tmp_path, "2026-01-18", "commit", "-qam", "fix login")
    _dated(tmp_path, "2026-03-01", "checkout", "-q", "main")
    (tmp_path / "notes.txt").write_text("n\n", encoding="utf-8")
    _dated(tmp_path, "2026-03-01", "add", "notes.txt")
    _dated(tmp_path, "2026-03-01", "commit", "-qm", "notes")
    land = "2026-08-12"
    if request.param == "merge":
        _dated(tmp_path, land, "merge", "-q", "--no-ff", "feat", "-m", "Merge pull request #7")
    elif request.param == "squash":
        _dated(tmp_path, land, "merge", "-q", "--squash", "feat")
        _dated(tmp_path, land, "commit", "-qm", "fix login (#7)", authored="2026-01-18")
    else:
        _dated(tmp_path, land, "rebase", "-q", "main", "feat")
        _dated(tmp_path, land, "checkout", "-q", "main")
        _dated(tmp_path, land, "merge", "-q", "--ff-only", "feat")
    (tmp_path / "notes.txt").write_text("n\nm\n", encoding="utf-8")
    _dated(tmp_path, "2026-09-01", "commit", "-qam", "more notes")
    head = _dated(tmp_path, "2026-09-01", "rev-parse", "HEAD")
    await session.execute(
        update(Repository).where(Repository.id == "repo1").values(head_commit=head)
    )
    await session.commit()
    return request.param


@pytest.mark.asyncio
async def test_symbol_last_change_is_dated_by_the_day_its_change_landed(late_landing):
    from repowise.server.mcp_server import get_context

    target = "src/auth/service.py::login"
    own = (await get_context(targets=[target], include=["last_change"]))["targets"][target][
        "last_change"
    ]
    assert own["date"].startswith("2026-08-12")
    assert own["authored"] == "2026-01-18"


@pytest.mark.asyncio
async def test_symbol_last_change_keeps_one_date_when_git_cannot_tell_the_landing(
    late_landing, monkeypatch
):
    from repowise.server.mcp_server import _changes, get_context

    _changes._landed_at.cache_clear()
    monkeypatch.setattr(_changes, "landing_date", lambda *a, **k: None)
    target = "src/auth/service.py::login"
    own = (await get_context(targets=[target], include=["last_change"]))["targets"][target][
        "last_change"
    ]
    assert "authored" not in own


async def _landed_change(session, *, merged: datetime, written: datetime) -> None:
    """A stored change touching service.py, merged at *merged*, whose commit on
    the file was made at *written*, the file's newest commit."""
    from repowise.core.persistence.models import GitCommit, GitCommitFile

    sha = "7" * 40
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(last_commit_at=written)
    )
    session.add(GitCommit(repository_id="repo1", sha=sha, author_name="Dev", committed_at=written))
    session.add(GitCommitFile(repository_id="repo1", sha=sha, file_path="src/auth/service.py"))
    await _seed(session, [_row("pr-7", 7, ["src/auth/service.py"], sha=sha, merged_at=merged)])


@pytest.mark.asyncio
async def test_file_last_change_is_dated_by_the_merge_that_landed_it(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    await _landed_change(
        session, merged=datetime(2026, 8, 12, tzinfo=UTC), written=datetime(2026, 1, 18, tzinfo=UTC)
    )
    path = "src/auth/service.py"
    last = (await get_context([path], include=["last_change"]))["targets"][path]["last_change"]
    assert last["date"].startswith("2026-08-12")
    assert last["authored"] == "2026-01-18"


@pytest.mark.asyncio
async def test_file_last_change_written_and_landed_together_is_unchanged(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    when = datetime(2026, 1, 18, tzinfo=UTC)
    path = "src/auth/service.py"
    await _landed_change(session, merged=when, written=when)
    last = (await get_context([path], include=["last_change"]))["targets"][path]["last_change"]
    assert last["date"].startswith("2026-01-18")
    assert "authored" not in last


@pytest.mark.asyncio
async def test_file_last_change_without_the_changes_commits_keeps_its_own_date(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    path = "src/auth/service.py"
    written = datetime(2026, 1, 18, tzinfo=UTC)
    await session.execute(
        update(GitMetadata).where(GitMetadata.file_path == path).values(last_commit_at=written)
    )
    # The change's commit is not in the stored history: its author is unknown.
    await _seed(
        session,
        [_row("pr-7", 7, [path], sha="8" * 40, merged_at=datetime(2026, 8, 12, tzinfo=UTC))],
    )
    last = (await get_context([path], include=["last_change"]))["targets"][path]["last_change"]
    assert last["date"].startswith("2026-01-18")
    assert "authored" not in last
