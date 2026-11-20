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
    assert data["prose_source"] == {"writer": "template", "written_at": None, "cost_usd": None}


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


def test_do_these_first_ranks_act_now_items_across_sections() -> None:
    # Four sections each with an act_now item: the ranking, not the section
    # order, decides which three lead.
    inferred = [
        _item(section, "r", f"{section}.py", severity="act_now", priority=1.0)
        for section in ("security", "performance", "health")
    ]
    measured = _item("dead_code", "r", "d.py", basis="measured", severity="act_now")
    report = _report(*inferred, measured)
    assert report.item(report.top[0]).section == "dead_code"
    assert len(report.top) == 3


def test_staleness_names_fixed_changed_and_new() -> None:
    kept, gone, moved = _item(path="k.py"), _item(path="g.py"), _item(path="m.py")
    report = _report(kept, gone, moved)
    now = [kept, _item(path="m.py", fingerprint="f2"), _item(path="n.py")]
    found = compare(report, now, current_commit="c2")
    assert found.fixed == (gone.id,)
    assert found.changed == (moved.id,)
    assert [i.fingerprint for i in found.changed_items] == ["f2"]
    assert [i.id for i in found.new] == [_item(path="n.py").id]
    assert found.behind


def test_staleness_does_not_call_a_section_new_when_the_report_could_not_read_it() -> None:
    report = _report(_item(path="k.py"))
    report = FirstLookReport.from_json({**report.to_json(), "unavailable": {"docs": "unreadable"}})
    found = compare(report, [_item(path="k.py"), _item("docs", "broken_doc_refs", "README.md")])
    assert found.new == ()


def test_staleness_says_nothing_about_a_section_it_cannot_read() -> None:
    report = _report(_item(path="k.py"), _item("docs", "broken_doc_refs", "README.md"))
    found = compare(report, [_item(path="k.py")], unknown={"docs": "unreadable"})
    assert found.fixed == () and found.new == ()
    assert not found.behind  # no current commit known


def test_the_file_store_keeps_the_report_of_another_commit(tmp_path) -> None:
    first = _report(_item(), commit="a" * 40)
    write_report_file(tmp_path, first)
    assert read_report_file(tmp_path).to_json() == first.to_json()
    second = _report(_item(path="b.py"), commit="b" * 40)
    path = write_report_file(tmp_path, second)
    assert path == report_path(tmp_path)
    assert read_report_file(tmp_path).stamp.commit == "b" * 40
    kept = json.loads((path.parent / f"first-look.{'a' * 40}.json").read_text(encoding="utf-8"))
    assert kept["stamp"]["commit"] == "a" * 40


def test_a_failed_final_replace_leaves_the_current_report(tmp_path, monkeypatch) -> None:
    from repowise.core.report.first_look import store

    first = _report(_item(), commit="a" * 40)
    write_report_file(tmp_path, first)

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", fail)
    with pytest.raises(OSError):
        write_report_file(tmp_path, _report(_item(path="b.py"), commit="b" * 40))
    assert read_report_file(tmp_path).to_json() == first.to_json()
    assert (report_path(tmp_path).parent / f"first-look.{'a' * 40}.json").exists()


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


def test_ownership_section_is_on() -> None:
    from repowise.core.report.first_look.sections import ownership

    assert ownership.OWNERSHIP_ENABLED is True


async def test_ownership_counts_components_at_bus_factor_one(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from repowise.core.report.first_look.sections import ownership
    from repowise.core.report.sections import bus_factor

    async def fake_bus_factor(_ctx):
        components = {
            "api": {"value": 1, "basis": "measured"},
            "web": {"value": 3, "basis": "measured"},
            "cli": {"value": None, "basis": "unavailable"},
        }
        return [{"data": {"components": components}}]

    monkeypatch.setattr(bus_factor, "collect", fake_bus_factor)
    ctx = SimpleNamespace(session=None, repository_id="r1", repo_root=tmp_path)
    got = await ownership.collect(ctx)
    (item,) = got.items
    assert item.title_facts == {"components": 2, "bus_factor_1": 1}
    assert item.link.route == "ownership"
    assert "api" not in json.dumps(item.to_json())


def _member_item(rule: str, path: str, ref: str, *, fingerprint: str = "f1") -> Item:
    import dataclasses

    from repowise.core.report.first_look.model import Member

    return dataclasses.replace(
        _item(rule=rule, path=path, severity="act_now", fingerprint=fingerprint),
        prompt=PromptRef("action", ref),
        members=(Member("action", ref, "fp"),),
    )


def test_one_fix_groups_its_rules_into_one_item() -> None:
    password = _member_item("hardcoded_password", "b.py", "act_b")
    secret = _member_item("hardcoded_secret", "a.py", "act_a")
    other = _item(section="docs", rule="broken_doc_refs", path="README.md")
    grouped = rank.group_items([password, other, secret])

    assert [i.section for i in grouped] == ["docs", "security"]
    group = grouped[1]
    assert group.id == item_id("security", "credentials", "")
    assert set(group.rules) == {"hardcoded_password", "hardcoded_secret"}
    assert group.rule in group.rules and group.rules[0] == group.rule
    assert [loc.path for loc in group.locations] == ["a.py", "b.py"]
    assert group.locations_total == 2 and group.title_facts["files"] == 2
    assert {m.ref for m in group.members} == {"act_a", "act_b"}
    # A group's prompt covers every location; one member keeps its own.
    assert group.prompt.renderer == "first_look_item"
    (alone,) = rank.group_items([password])
    assert alone.prompt == PromptRef("action", "act_b")
    assert alone.to_json()["rules"] == ["hardcoded_password"]


def test_a_group_id_holds_while_files_join_and_its_lines_move() -> None:
    one = rank.group_items([_member_item("hardcoded_password", "a.py", "act_a")])[0]
    two = rank.group_items(
        [
            _member_item("hardcoded_password", "a.py", "act_a"),
            _member_item("hardcoded_secret", "c.py", "act_c"),
        ]
    )[0]
    assert one.id == two.id
    assert one.fingerprint != two.fingerprint  # the report compares it as changed


def test_an_item_round_trips_its_rules_and_members() -> None:
    (group,) = rank.group_items(
        [
            _member_item("hardcoded_password", "a.py", "act_a"),
            _member_item("hardcoded_secret", "c.py", "act_c"),
        ]
    )
    assert Item.from_json(json.loads(json.dumps(group.to_json()))) == group


def test_a_failed_write_keeps_the_current_report(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    write_report_file(tmp_path, _report(_item(), commit="a" * 40))
    real = Path.write_text

    def failing(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError("disk full")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing)
    with pytest.raises(OSError):
        write_report_file(tmp_path, _report(_item(), commit="b" * 40))
    kept = read_report_file(tmp_path)
    assert kept is not None and kept.stamp.commit == "a" * 40
