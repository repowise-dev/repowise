"""The first-look template text: one writer per rule, written from facts only."""

from __future__ import annotations

import json
import typing

import pytest

from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS
from repowise.core.analysis.health.fix_first.model import FixKind
from repowise.core.report.first_look import rank
from repowise.core.report.first_look.model import (
    SECTION_CAP,
    FirstLookReport,
    Item,
    Link,
    Location,
    PromptRef,
    SectionBlock,
    Stamp,
    file_link,
    item_id,
)
from repowise.core.report.first_look.render import (
    render_first_look_html,
    render_first_look_markdown,
    route_path,
)
from repowise.core.report.first_look.text import (
    TITLES,
    generic_item,
    has_writer,
    item_text,
    template_prose,
)
from repowise.core.report.first_look.write import with_prose

#: Every ``(section, rule)`` the built-in sections emit; the performance rule
#: is any marker, so one stands for all.
BUILT_IN_RULES: list[tuple[str, str]] = [
    *(("security", k) for k in SECRET_KINDS),
    ("security", PUBLIC_ENV_KIND),
    ("performance", "io_in_loop"),
    ("performance", "membership_test_against_list_in_loop"),
    *(("health", k) for k in typing.get_args(FixKind) if k != "perf_fix"),
    ("health", "lowest_health_file"),
    ("tests", "fragile_measured_coverage"),
    ("dead_code", "safe_to_delete"),
    ("dead_code", "unreachable_file"),
    ("docs", "broken_doc_refs"),
    ("decisions", "stale_decision"),
    ("hygiene", "import_cycle"),
    ("hygiene", "vendored_in_tree"),
    ("ownership", "bus_factor"),
]

_FACTS: dict[str, dict] = {
    "hardcoded_password": {"kind": "hardcoded_password", "locations": 1},
    "hardcoded_secret": {"kind": "hardcoded_secret", "locations": 2},
    PUBLIC_ENV_KIND: {"kind": PUBLIC_ENV_KIND, "locations": 1},
    "io_in_loop": {"symbol": "load", "role": "request", "call_sites": 2, "steps": 1},
    "membership_test_against_list_in_loop": {"symbol": "scan", "call_sites": 1, "steps": 1},
    "refactor": {
        "symbol": "build",
        "title": "Split build into 6 helpers",
        "facts": {"finding": "Complex method", "size": "CCN 49, 202 lines",
                  "files that import its file": "1", "changes in 90 days": "3"},
        "verify": "pytest tests/test_build.py",
    },
    "finding": {
        "symbol": "Big",
        "title": "Break up Big (CCN 138, 536 lines)",
        "facts": {"finding": "Complex method", "size": "CCN 138, 536 lines",
                  "files that import its file": "75", "changes in 90 days": "6"},
        "verify": None,
    },
    "lowest_health_file": {"score": 1.0, "band": "at_risk", "nloc": 300},
    "fragile_measured_coverage": {"line_pct": 12.5, "fix_commits_90d": 4, "commits_90d": 9},
    "safe_to_delete": {"symbols": 3, "lines": 30},
    "unreachable_file": {"lines": 40, "check_first": True},
    "broken_doc_refs": {"references": 2, "kinds": ["anchor"],
                        "targets": ["docs/a.md#gone", "docs/a.md#also-gone"]},
    "stale_decision": {"title": "Use one queue"},
    "import_cycle": {"files": 2},
    "vendored_in_tree": {"files": 1},
    "bus_factor": {"components": 4, "bus_factor_1": 1},
}


def _item(section: str, rule: str, path: str = "docs/a.md", *, severity: str = "plan") -> Item:
    return Item(
        id=item_id(section, rule, path),
        section=section,
        rule=rule,
        title_facts=_FACTS.get(rule, {}),
        locations=(Location(path, 3, "f"), Location(path, 9, "g")),
        locations_total=2,
        confidence="high",
        basis="inferred",
        severity=severity,
        effort="S",
        link=file_link(path, 3),
        prompt=PromptRef("first_look_item"),
        fingerprint="f",
    )


def _report(items: list[Item], facts: dict[str, dict] | None = None) -> FirstLookReport:
    by: dict[str, list[Item]] = {}
    for i in items:
        by.setdefault(i.section, []).append(i)
    blocks = tuple(
        SectionBlock(k, rank.order_items(v), (facts or {}).get(k, {})) for k, v in by.items()
    )
    return FirstLookReport(
        stamp=Stamp("a" * 40, "2026-11-20T00:00:00+00:00", "2026-11-20T00:00:00+00:00"),
        repo={"name": "shop", "files": 10, "health": {"score": 7.7, "band_label": "Good"}},
        sections=blocks,
        top=rank.top_items(blocks),
    )


@pytest.mark.parametrize(("section", "rule"), BUILT_IN_RULES)
def test_every_built_in_rule_has_its_own_text(section: str, rule: str) -> None:
    assert has_writer(section, rule)
    item = _item(section, rule, severity="act_now" if section == "security" else "plan")
    text = item_text(item)
    assert text and text != generic_item(item)
    assert "—" not in text and "None" not in text


def test_a_rule_no_writer_knows_reads_the_explicit_fallback() -> None:
    item = _item("plugin_section", "license_risk", "src/x.py")
    assert not has_writer("plugin_section", "license_risk")
    assert item_text(item) == generic_item(item) == "License risk in `src/x.py`."


def test_prose_has_the_contract_shape_and_covers_every_item() -> None:
    items = [_item(s, r, f"src/{r}.py") for s, r in BUILT_IN_RULES]
    report = _report(items)
    prose = template_prose(report)
    assert prose["writer"] == "template"
    assert set(prose) >= {"writer", "summary", "sections", "items", "setup"}
    assert set(prose["items"]) == {i.id for i in items}
    assert set(prose["sections"]) == {s.key for s in report.sections}
    assert prose["titles"]["health"] == TITLES["health"]
    assert "—" not in json.dumps(prose, ensure_ascii=False)


def test_security_says_rotate_and_prints_no_value() -> None:
    report = _report([_item("security", "hardcoded_password", "cfg.py", severity="act_now")])
    prose = template_prose(report)
    intro = prose["sections"]["security"]
    assert "git history" in intro and "Rotate" in intro
    assert "value redacted" in next(iter(prose["items"].values()))


def test_empty_security_says_no_live_secrets() -> None:
    block = SectionBlock("security", (), {"history_only": 0})
    report = FirstLookReport(Stamp(None, None, "t"), {"name": "r"}, (block,))
    assert template_prose(report)["sections"]["security"] == "No live secrets in tracked files."


def test_numbers_carry_units_and_denominators() -> None:
    perf = [_item("performance", "io_in_loop", "src/a.py")]
    report = _report(perf, {"performance": {"production_causes": 235}})
    assert template_prose(report)["sections"]["performance"] == (
        "1 cause is worth fixing now, of 235 found in production code."
    )
    anchors = item_text(_item("docs", "broken_doc_refs", "docs/a.md"))
    assert anchors == "2 anchors that match no heading: `#gone`, `#also-gone`."


def test_markdown_caps_a_section_and_groups_dead_code_by_file() -> None:
    items = [_item("hygiene", "import_cycle", f"src/m{n}.py") for n in range(SECTION_CAP + 3)]
    dead = _item("dead_code", "safe_to_delete", "src/dead.py")
    report = with_prose(_report([*items, dead]))
    doc = {**report.to_json(), "prompts": {}, "app_url": None}
    md = render_first_look_markdown(doc)
    assert "3 more in the app." in md
    assert "`src/dead.py:3` (f)" in md  # one chip per place, its symbols beside it
    assert "](" not in md  # no server known: plain path:line, no links


def test_links_come_from_route_keys_when_the_app_is_known() -> None:
    item = _item("hygiene", "import_cycle", "src/a b.py")
    report = with_prose(_report([item]))
    doc = {**report.to_json(), "prompts": {item.id: "Do it."}, "app_url": "http://h:3000/repos/r1"}
    md = render_first_look_markdown(doc)
    assert "[`src/a b.py:3`](http://h:3000/repos/r1/files/src/a%20b.py?line=3)" in md
    # The Markdown export carries no prompt; the HTML page does.
    assert "<details>" not in md and "Do it." not in md
    html = render_first_look_html(doc)
    assert "Do it." in html
    assert 'href="http://h:3000/repos/r1/files/src/a%20b.py?line=3"' in html
    assert "default-src 'none'" in html and "<script" not in html
    assert "prefers-color-scheme: dark" in html
    assert route_path("dead_code", {}) == "code-health?tab=dead-code"
    assert route_path("decision", {"id": "d/1"}) == "decisions/d%2F1"
    assert route_path(Link("health_file", {"path": "a.py"}).route, {"path": "a.py"}) == (
        "code-health?file=a.py"
    )


def test_only_act_now_is_marked() -> None:
    now = _item("security", "hardcoded_password", "a.py", severity="act_now")
    later = _item("hygiene", "import_cycle", "b.py")
    report = with_prose(_report([now, later]))
    html = render_first_look_html({**report.to_json(), "prompts": {}})
    assert html.count('class="badge now"') == 1


def test_assessment_formats_leave_the_first_look_renderers_out() -> None:
    import click

    from repowise.cli.commands.report_assessment_cmd import _renderer

    with pytest.raises(click.BadParameter) as err:
        _renderer("first-look-html")
    assert "first-look" not in str(err.value).split("available:", 1)[1]


def test_stored_prose_keeps_its_text_and_gaps_read_the_template() -> None:
    item = _item("hygiene", "import_cycle", "src/a.py")
    report = _report([item])
    older = FirstLookReport.from_json(
        {**report.to_json(), "prose": {"writer": "m1", "summary": "", "items": {}}}
    )
    prose = with_prose(older).prose
    assert prose["writer"] == "m1"
    assert prose["summary"] == template_prose(report)["summary"]
    assert prose["items"][item.id] == item_text(item)
