"""The first-look report's pure parts: model, ids, ranking, staleness, file store, settings."""

from __future__ import annotations

import json

import pytest

from repowise.core.report.first_look import rank
from repowise.core.report.first_look.config import first_look_settings
from repowise.core.report.first_look.model import (
    SCHEMA_VERSION,
    FirstLookReport,
    Item,
    Link,
    Location,
    PromptRef,
    SectionBlock,
    SetupStep,
    Stamp,
    file_link,
    item_id,
)
from repowise.core.report.first_look.staleness import compare
from repowise.core.report.first_look.store import read_report_file, report_path, write_report_file


def _item(
    section: str = "security",
    rule: str = "hardcoded_password",
    path: str = "a.py",
    *,
    basis: str = "inferred",
    severity: str = "plan",
    priority: float = 1.0,
    weight: float = 0.0,
    fingerprint: str = "f1",
    line: int | None = 3,
) -> Item:
    return Item(
        id=item_id(section, rule, path),
        section=section,
        rule=rule,
        title_facts={"n": 1},
        locations=(Location(path, line),),
        confidence="high",
        basis=basis,
        severity=severity,
        effort="S",
        link=file_link(path, line),
        prompt=PromptRef("first_look_item"),
        fingerprint=fingerprint,
        priority=priority,
        weight=weight,
    )


def _report(*items: Item, commit: str = "c1") -> FirstLookReport:
    sections: dict[str, list[Item]] = {}
    for i in items:
        sections.setdefault(i.section, []).append(i)
    blocks = tuple(SectionBlock(k, rank.order_items(v)) for k, v in sections.items())
    return FirstLookReport(
        stamp=Stamp(commit, None, "2026-11-20T00:00:00+00:00"),
        repo={"name": "r"},
        sections=blocks,
        top=rank.top_items(blocks),
        setup=(SetupStep("act_1", "coverage_missing", "Add a coverage report", "repowise coverage add"),),
    )


def test_item_id_ignores_the_line_and_is_stable() -> None:
    a = item_id("dead_code", "safe_to_delete", "src/x.py", "f")
    assert a == item_id("dead_code", "safe_to_delete", "src/x.py", "f")
    assert a.startswith("fl_") and len(a) == 15
    assert _item(line=3).id == _item(line=90).id
    assert a != item_id("dead_code", "safe_to_delete", "src/x.py", "g")


def test_report_json_round_trips() -> None:
    report = _report(_item(), _item("dead_code", "safe_to_delete", "b.py"))
    data = json.loads(json.dumps(report.to_json()))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["sections"][0]["items"][0]["source_layer"] == "security"
    assert FirstLookReport.from_json(data).to_json() == report.to_json()


def test_a_newer_schema_is_refused() -> None:
    data = _report(_item()).to_json()
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="newer"):
        FirstLookReport.from_json(data)


def test_vocabularies_are_closed() -> None:
    with pytest.raises(ValueError):
        Link("https://example.com")
    with pytest.raises(ValueError):
        PromptRef("free_text")
    with pytest.raises(ValueError):
        _item(basis="guessed")


def test_a_section_ranks_measured_first_then_tier_then_priority() -> None:
    inferred_now = _item("performance", "a", "1.py", severity="act_now", priority=0.1)
    measured = _item("performance", "b", "2.py", basis="measured", priority=0.1)
    high = _item("performance", "c", "3.py", priority=5.0)
    low = _item("performance", "d", "4.py", priority=1.0, weight=9)
    ordered = rank.order_items([low, high, inferred_now, measured])
    assert [i.rule for i in ordered] == ["b", "a", "c", "d"]


def test_do_these_first_takes_act_now_then_one_per_section() -> None:
    secret = _item("security", "hardcoded_password", "s.py", severity="act_now")
    secret2 = _item("security", "hardcoded_password", "t.py", severity="act_now")
    perf = _item("performance", "io_in_loop", "p.py", basis="measured", priority=0.5)
    health = _item("health", "finding", "h.py", priority=3.0)
    dead = _item("dead_code", "safe_to_delete", "d.py", priority=2.0)
    report = _report(secret, secret2, perf, health, dead)
    sections = [report.item(i).section for i in report.top]
    assert sections == ["security", "performance", "health"]


def test_staleness_names_fixed_changed_and_new() -> None:
    kept, gone, moved = _item(path="k.py"), _item(path="g.py"), _item(path="m.py")
    report = _report(kept, gone, moved)
    now = [kept, _item(path="m.py", fingerprint="f2"), _item(path="n.py")]
    found = compare(report, now, current_commit="c2")
    assert found.fixed == (gone.id,)
    assert found.changed == (moved.id,)
    assert [i.id for i in found.new] == [_item(path="n.py").id]
    assert found.behind


def test_staleness_says_nothing_about_a_section_it_cannot_read() -> None:
    report = _report(_item(path="k.py"), _item("docs", "broken_doc_refs", "README.md"))
    found = compare(report, [_item(path="k.py")], unknown={"docs": "unreadable"})
    assert found.fixed == () and found.new == ()
    assert not found.behind  # no current commit known


def test_the_file_store_keeps_the_report_of_another_commit(tmp_path) -> None:
    first = _report(_item(), commit="c1")
    write_report_file(tmp_path, first)
    assert read_report_file(tmp_path).to_json() == first.to_json()
    second = _report(_item(path="b.py"), commit="c2")
    path = write_report_file(tmp_path, second)
    assert path == report_path(tmp_path)
    assert read_report_file(tmp_path).stamp.commit == "c2"
    kept = json.loads((path.parent / "first-look.c1.json").read_text(encoding="utf-8"))
    assert kept["stamp"]["commit"] == "c1"


def test_an_unreadable_report_file_is_kept_aside(tmp_path) -> None:
    path = report_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_report_file(tmp_path) is None
    write_report_file(tmp_path, _report(_item()))
    assert (path.parent / "first-look.previous.json").read_text(encoding="utf-8") == "{not json"


def test_settings_default_and_read_the_config(tmp_path) -> None:
    assert first_look_settings(tmp_path, {}) == first_look_settings(tmp_path, {"report": {}})
    defaults = first_look_settings(tmp_path, {})
    assert (defaults.enabled, defaults.llm, defaults.model) == (True, True, "")
    off = first_look_settings(
        tmp_path, {"report": {"first_look": {"llm": False, "model": "claude-haiku-5-5"}}}
    )
    assert (off.llm, off.model) == (False, "claude-haiku-5-5")


def test_ownership_is_off_until_author_identity_is_fixed() -> None:
    from repowise.core.report.first_look.sections import ownership

    assert ownership.OWNERSHIP_ENABLED is False
