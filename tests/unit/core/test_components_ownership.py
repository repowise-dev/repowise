"""Declared against observed ownership per component: the four findings."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from repowise.core.components.catalog import Component
from repowise.core.components.ownership import Activity, assess_component, build_activity
from repowise.core.ownership.codeowners import parse_codeowners
from repowise.core.ownership.directory import DeclaredOwnership, Team

_FILES = ["src/pay/a.py", "src/pay/b.py"]
_AS_OF = datetime(2026, 10, 1, tzinfo=UTC)
_HISTORY = [
    ("Ana Lima", "ana@acme.com"),
    ("Li Wu", "li@acme.com"),
    ("Old Hand", "old@acme.com"),
    ("Kim", "123+kim-gh@users.noreply.github.com"),
    ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
]


def _activity(recent: list[tuple[str, str, str, str]]) -> Activity:
    return build_activity(_HISTORY, recent, _AS_OF)


def _declared(codeowners: str = "", teams: list[Team] | None = None) -> DeclaredOwnership:
    return DeclaredOwnership(
        teams={t.name.lower(): t for t in teams or []},
        codeowners_file=".github/CODEOWNERS",
        rules=parse_codeowners(codeowners),
    )


def _comp(**kw) -> Component:
    return Component(name="pay", paths=("src/pay/**",), **kw)


def _kinds(block: dict) -> list[str]:
    return [f["kind"] for f in block["findings"]]


def test_active_team_owner_has_no_findings() -> None:
    team = Team("payments", emails=("ana@acme.com",))
    block = assess_component(
        _comp(owner="group:default/payments", criticality="critical"),
        _FILES,
        _declared(teams=[team]),
        _activity([("src/pay/a.py", "s1", "Ana Lima", "ana@acme.com")]),
    )
    assert block["findings"] == []
    assert block["declared_teams"] == [{"team": "payments", "files": 2, "activity": "active"}]
    assert block["recent"] == {
        "commits": 1,
        "contributors": 1,
        "window_days": 90,
        "as_of": "2026-10-01",
    }
    assert block["files_with_declared_owner"] == 2


def test_inactive_team_and_critical_without_active_owner() -> None:
    team = Team("payments", logins=("old-gh",), emails=("old@acme.com",))
    block = assess_component(
        _comp(owner="payments", criticality="high"),
        _FILES,
        _declared(teams=[team]),
        _activity([("src/pay/a.py", "s1", "Li Wu", "li@acme.com")]),
    )
    assert _kinds(block) == ["declared_owner_inactive", "critical_no_active_owner"]
    assert "'payments'" in block["findings"][0]["message"]


def test_owner_with_no_known_identity_is_unknown_not_inactive() -> None:
    teams = [Team("payments"), Team("ghosts", emails=("nobody@acme.com",))]
    block = assess_component(
        _comp(owner="payments", criticality="critical"),
        _FILES,
        _declared("/src/pay/ @acme/ghosts\n", teams),
        _activity([]),
    )
    assert {t["activity"] for t in block["declared_teams"]} == {"unknown"}
    assert block["findings"] == []


def test_critical_with_no_declared_owner() -> None:
    block = assess_component(_comp(criticality="critical"), _FILES, _declared(), _activity([]))
    assert _kinds(block) == ["critical_no_active_owner"]
    # Not critical: nothing to report.
    block = assess_component(_comp(criticality="medium"), _FILES, _declared(), _activity([]))
    assert block["findings"] == []


def test_individual_owners_are_counted_never_named() -> None:
    codeowners = "/src/pay/a.py @kim-gh\n/src/pay/b.py old@acme.com\n"
    recent = [
        ("src/pay/a.py", "s1", "Kim", "999+kim-gh@users.noreply.github.com"),
        ("src/pay/b.py", "s2", "Li Wu", "li@acme.com"),
    ]
    block = assess_component(_comp(), _FILES, _declared(codeowners), _activity(recent))
    assert block["declared_individuals"] == {"count": 2, "no_recent_changes": 1, "unknown": 0}
    assert _kinds(block) == ["declared_owner_inactive"]
    text = json.dumps(block)
    for person in ("kim", "old@", "Kim", "Li Wu", "li@acme"):
        assert person not in text


def test_bot_changes_are_not_activity() -> None:
    team = Team("payments", emails=("ana@acme.com",))
    bot = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
    recent = [("src/pay/a.py", "s1", *bot)]
    block = assess_component(
        _comp(owner="payments"), _FILES, _declared(teams=[team]), _activity(recent)
    )
    assert block["declared_teams"][0]["activity"] == "no_recent_changes"
    assert block["recent"]["contributors"] == 0


def test_critical_bus_factor_one_only_when_measured() -> None:
    team = Team("payments", emails=("ana@acme.com",))
    recent = _activity([("src/pay/a.py", "s", "Ana Lima", "ana@acme.com")])
    args = (_FILES, _declared(teams=[team]), recent)
    comp = _comp(owner="payments", criticality="critical")
    measured = assess_component(comp, *args, {"value": 1, "basis": "measured"})
    assert _kinds(measured) == ["critical_bus_factor_one"]
    inferred = assess_component(comp, *args, {"value": 1, "basis": "inferred"})
    assert inferred["findings"] == []


def test_catalog_entry_with_no_code() -> None:
    block = assess_component(_comp(criticality="critical"), [], _declared(), _activity([]))
    assert _kinds(block) == ["catalog_entry_no_code"]
    derived = Component(name="lib", source="directory", root="lib")
    assert assess_component(derived, [], _declared(), _activity([]))["findings"] == []


def test_no_history_leaves_activity_unavailable() -> None:
    block = assess_component(_comp(owner="payments"), _FILES, _declared(), Activity())
    assert block["basis"] == "unavailable"
    assert "recent" not in block
    assert block["declared_teams"][0]["activity"] == "unknown"


def test_unresolved_bare_handle_counts_as_owner_but_raises_nothing() -> None:
    declared = _declared("/src/pay/ @payments-group\n")
    declared.codeowners_file = ".gitlab/CODEOWNERS"
    block = assess_component(_comp(criticality="critical"), _FILES, declared, _activity([]))
    assert block["declared_unresolved"] == 1
    assert block["findings"] == []
    assert "payments-group" not in json.dumps(block)
