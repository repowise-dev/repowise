"""The owners summary, the knowledge matrix and the concentration they share.

All three read people, never automation: a bot's commits and owned files
leave every share, on both sides of the fraction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.analysis.knowledge_matrix import (
    build_knowledge_matrix,
    file_author_shares,
)
from repowise.core.analysis.owners import aggregate_owners, people_resolver
from repowise.core.analysis.owners_summary import build_owner_summary
from repowise.core.analysis.ownership_concentration import (
    module_silos,
    owned_files,
    person_owner,
    top_owners,
    truck_factor,
)
from repowise.core.stats_highlights import build_people

_HEAD = datetime(2026, 9, 1, tzinfo=UTC)
_BOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
_ADA = ("Ada Lovelace", "ada@example.com")
_ADA_ALT = ("Ada Lovelace", "1234+ada@users.noreply.github.com")
_BOB = ("Bob Stone", "bob@example.com")
_CY = ("Cy Young", "cy@example.com")


def _author(who: tuple[str, str], commits: int) -> dict:
    return {"name": who[0], "email": who[1], "commit_count": commits}


def _row(
    path: str,
    owner: tuple[str, str],
    authors: list[dict],
    *,
    bus_factor: int = 1,
    hotspot: bool = False,
    changed_90d: int = 0,
) -> dict:
    return {
        "file_path": path,
        "primary_owner_name": owner[0],
        "primary_owner_email": owner[1],
        "top_authors_json": authors,
        "bus_factor": bus_factor,
        "is_hotspot": hotspot,
        "commit_count_90d": changed_90d,
    }


def _commit(sha: str, who: tuple[str, str], days_before_head: int) -> dict:
    return {
        "sha": sha,
        "author_name": who[0],
        "author_email": who[1],
        "committed_at": _HEAD - timedelta(days=days_before_head),
    }


# Ada owns the core (one file under her noreply address); Bob owns lib and
# has gone quiet while it still changes; Cy owns one file; the bot owns two.
ROWS = [
    _row("core/a.py", _ADA, [_author(_ADA, 8), _author(_BOT, 10), _author(_BOB, 2)], hotspot=True),
    _row("core/b.py", _ADA_ALT, [_author(_ADA_ALT, 3), _author(_ADA, 3)]),
    _row("core/c.py", _ADA, [_author(_ADA, 5), _author(_CY, 5)], bus_factor=2),
    _row("lib/x.py", _BOB, [_author(_BOB, 9), _author(_ADA, 1)], changed_90d=3),
    _row("lib/y.py", _BOB, [_author(_BOB, 4)], changed_90d=1, hotspot=True),
    _row("tools/t.py", _CY, [_author(_CY, 2)]),
    _row("deps/lock.py", _BOT, [_author(_BOT, 40)]),
    _row("deps/pin.py", _BOT, [_author(_BOT, 12)]),
]
COMMITS = [
    _commit("s1", _ADA, 1),
    _commit("s2", _ADA_ALT, 5),
    _commit("s3", _CY, 30),
    _commit("s4", _BOT, 2),
    _commit("s5", _BOB, 200),
]


def _fold():
    resolve = people_resolver(ROWS, COMMITS)
    accs, _ = aggregate_owners(ROWS, [], COMMITS, total_commits=len(COMMITS), resolve=resolve)
    return resolve, accs


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


def test_owned_files_counts_people_and_leaves_automation_out() -> None:
    owned = owned_files(ROWS, people_resolver(ROWS, COMMITS))
    assert dict(owned.by_owner) == {"ada@example.com": 3, "bob@example.com": 2, "cy@example.com": 1}
    assert owned.owned_total == 6
    assert owned.single_owner_files == 5
    assert sum(owned.module_totals.values()) == len(ROWS)


def test_truck_factor_silos_and_top_owners() -> None:
    owned = owned_files(ROWS, people_resolver(ROWS, COMMITS))
    # Ada's 3 of 6 is not more than half; Ada and Bob are.
    assert truck_factor(owned) == 2
    assert [(s.module, s.owner, s.owned, s.file_count) for s in module_silos(owned)] == [
        ("core", "ada@example.com", 3, 3),
        ("lib", "bob@example.com", 2, 2),
        ("tools", "cy@example.com", 1, 1),
    ]
    assert top_owners(owned, 2) == [("ada@example.com", 3), ("bob@example.com", 2)]
    assert truck_factor(owned_files([], people_resolver())) is None


def test_build_people_reads_the_shared_concentration() -> None:
    people = build_people(ROWS, COMMITS)
    assert people == {
        "owner_count": 3,
        "tracked_files": 8,
        "single_owner_files": 5,
        "silo_count": 3,
        "truck_factor": 2,
    }


# ---------------------------------------------------------------------------
# Identity on the owner fold
# ---------------------------------------------------------------------------


def test_owner_fold_carries_kind_and_merged_identities() -> None:
    _resolve, accs = _fold()
    ada = accs["ada@example.com"]
    assert ada.kind == "human"
    assert ada.alias_names == ["Ada Lovelace"]
    assert ada.alias_emails == ["ada@example.com", "ada@users.noreply.github.com"]
    bot = next(a for a in accs.values() if a.name.startswith("dependabot"))
    assert bot.kind == "bot"
    assert accs["bob@example.com"].owned_changing_files == 2


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_is_people_only() -> None:
    resolve, accs = _fold()
    summary = build_owner_summary(ROWS, resolve, accs)

    assert summary["truck_factor"] == build_people(ROWS, COMMITS)["truck_factor"] == 2
    assert (summary["people"], summary["automation_identities"]) == (3, 1)
    assert (summary["owned_files"], summary["tracked_files"]) == (6, 8)
    assert summary["single_owner_files"] == 5
    assert summary["single_owner_hotspots"] == 2

    concentration = summary["concentration"]
    # The bot's two files are in neither the numerator nor the denominator.
    assert concentration["share"] == 1.0
    assert [o["key"] for o in concentration["owners"]] == [
        "ada@example.com",
        "bob@example.com",
        "cy@example.com",
    ]
    assert concentration["owners"][0]["share"] == 0.5
    assert [s["module"] for s in summary["silo_modules"]] == ["core", "lib", "tools"]
    assert summary["silo_modules"][0]["owner"] == {"key": "ada@example.com", "name": "Ada Lovelace"}


def test_summary_activity_and_quiet_owners() -> None:
    resolve, accs = _fold()
    summary = build_owner_summary(ROWS, resolve, accs)
    assert summary["activity_complete"] is True
    # Ada and Cy committed in the window; the bot is not a person.
    assert summary["active_contributors"] == 2
    # Bob's last commit is 200 days old and he owns two files that still change.
    assert summary["quiet_owners_total"] == 1
    quiet = summary["quiet_owners"][0]
    assert (quiet["key"], quiet["changing_files_owned"], quiet["files_owned"]) == (
        "bob@example.com",
        2,
        2,
    )


def test_summary_without_commit_rows_names_nobody_quiet() -> None:
    resolve = people_resolver(ROWS)
    accs, _ = aggregate_owners(ROWS, [], resolve=resolve)
    summary = build_owner_summary(ROWS, resolve, accs)
    assert summary["activity_complete"] is False
    assert summary["active_contributors"] == 0
    assert summary["quiet_owners"] == []


# ---------------------------------------------------------------------------
# Knowledge matrix
# ---------------------------------------------------------------------------


def test_file_author_shares_merge_people_and_drop_automation() -> None:
    resolve = people_resolver(ROWS, COMMITS)
    # 10 of core/a.py's 20 commits are the bot's: Ada holds 8 of the 10 human ones.
    assert file_author_shares(ROWS[0], resolve) == [
        ("ada@example.com", 0.8),
        ("bob@example.com", 0.2),
    ]
    # Ada's two addresses are one author holding the whole file.
    assert file_author_shares(ROWS[1], resolve) == [("ada@example.com", 1.0)]
    assert file_author_shares(ROWS[6], resolve) == []
    assert person_owner(ROWS[1], resolve) == "ada@example.com"
    assert person_owner(ROWS[6], resolve) is None


def test_file_author_shares_keep_the_top_n() -> None:
    people = [(f"Person {i}", f"p{i}@example.com") for i in range(7)]
    row = _row("x.py", people[0], [_author(p, 10 - i) for i, p in enumerate(people)])
    shares = file_author_shares(row, people_resolver([row]), top=5)
    assert [k for k, _ in shares] == [f"p{i}@example.com" for i in range(5)]
    assert round(sum(s for _, s in shares), 6) == round(40 / 49, 6)


def test_knowledge_matrix_rows_are_positional_and_indexed() -> None:
    resolve, accs = _fold()
    matrix = build_knowledge_matrix(
        ROWS, resolve, accs, {"core/a.py": (6.234, 120), "lib/x.py": (None, 40)}
    )
    authors = [a["key"] for a in matrix["authors"]]
    # Indexed by how many files each person appears on, most first.
    assert authors == ["ada@example.com", "bob@example.com", "cy@example.com"]
    assert matrix["authors"][0]["active"] is True
    assert matrix["authors"][1]["active"] is False

    files = {
        (matrix["dirs"][row[0]] + "/" if matrix["dirs"][row[0]] else "") + row[1]: row
        for row in matrix["files"]
    }
    assert list(files) == sorted(r["file_path"] for r in ROWS)
    assert files["core/a.py"][:8] == (0, "a.py", 120, 6.2, True, 1, 0, [(0, 0.8), (1, 0.2)])
    assert files["lib/x.py"][2:4] == (40, None)
    # A bot-owned file keeps its row, with no owner and no authors.
    assert files["deps/lock.py"][6:8] == (None, [])
    # The last element says whether the file changed in the activity window.
    assert {path: row[8] for path, row in files.items()} == {
        r["file_path"]: (r.get("commit_count_90d") or 0) > 0 for r in ROWS
    }


def test_an_email_only_owner_owns_files_on_every_surface() -> None:
    rows = [_row("a.py", ("", "solo@example.com"), [_author(("", "solo@example.com"), 3)])]
    resolve = people_resolver(rows)
    accs, _ = aggregate_owners(rows, [], resolve=resolve)
    assert accs["solo@example.com"].files_owned == 1
    assert dict(owned_files(rows, resolve).by_owner) == {"solo@example.com": 1}
    assert person_owner(rows[0], resolve) == "solo@example.com"


def test_a_file_without_history_is_not_a_single_owner_risk() -> None:
    rows = [*ROWS, _row("core/new.py", _ADA, [], bus_factor=0)]
    resolve = people_resolver(rows, COMMITS)
    accs, _ = aggregate_owners(rows, [], COMMITS, len(COMMITS), resolve)
    ada = accs["ada@example.com"]
    # a.py and b.py have bus factor 1; c.py has 2 and new.py has none indexed.
    assert (ada.files_owned, ada.bus_factor_risk_files) == (4, 2)
    assert build_owner_summary(rows, resolve, accs)["single_owner_files"] == 5


def test_automation_and_unowned_files_are_not_single_owner_risks() -> None:
    rows = [
        *ROWS,
        _row("deps/hot.py", _BOT, [_author(_BOT, 30)], hotspot=True),
        _row("gen/out.py", ("", ""), [], hotspot=True),
    ]
    resolve = people_resolver(rows, COMMITS)
    accs, _ = aggregate_owners(rows, [], COMMITS, len(COMMITS), resolve)
    owned = owned_files(rows, resolve)
    assert (owned.single_owner_files, owned.single_owner_hotspots) == (5, 2)
    summary = build_owner_summary(rows, resolve, accs)
    assert (summary["single_owner_files"], summary["single_owner_hotspots"]) == (5, 2)
