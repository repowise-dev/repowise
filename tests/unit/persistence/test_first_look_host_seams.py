"""The seams a host builds the repo report through: the "Do these first"
summary, sections it never stored, and every agent flavor in one pass."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

from repowise.core.agent_prompts import Flavor
from repowise.core.persistence.crud.analysis import actions as actions_crud
from repowise.core.persistence.crud.analysis import performance as performance_crud
from repowise.core.report.first_look.collect import collect_first_look
from repowise.core.report.first_look.living import build_living
from repowise.core.report.first_look.render import _md_top
from repowise.core.report.first_look.text import top_items_summary
from repowise.core.report.first_look.write import render_document, render_documents
from tests.unit.persistence.test_first_look_collect import _seed

NOW = datetime(2026, 11, 25, tzinfo=UTC)
FLAVORS = get_args(Flavor)
HOSTED = {"performance": "Shown on the Performance tab.", "security": "Shown on the Security tab."}


def _doc() -> dict:
    loc = lambda path, line=None: {"path": path, "line": line}  # noqa: E731
    return {
        "top": ["a", "gone", "b", "c"],
        "prose": {
            "titles": {"security": "Security", "health": "Code health: refactor first"},
            "items": {"a": "Rotate `KEY`\n  first.", "b": "Split it."},
        },
        "sections": [
            {
                "key": "security",
                "items": [
                    {
                        "id": "a",
                        "locations": [loc("s.py", 4), loc("s.py", 4), loc("t.py")],
                        "locations_total": 5,
                    },
                ],
            },
            {"key": "health", "items": [{"id": "b", "locations": []}]},
            {"key": "docs", "items": [{"id": "c", "locations": [loc("d.md", 2)]}]},
        ],
    }


def test_the_summary_lists_top_items_as_the_markdown_does() -> None:
    doc = _doc()
    rows = top_items_summary(doc)
    assert [
        (r["id"], r["section"], r["title"], r["text"], r["where"], r["more"]) for r in rows
    ] == [
        # Two places listed (s.py:4 twice is one place), two more not listed.
        ("a", "security", "Security", "Rotate `KEY` first.", "s.py:4", 3),
        ("b", "health", "Code health", "Split it.", None, 0),
        ("c", "docs", "docs", "", "d.md:2", 0),
    ]
    assert rows[0]["item"] is doc["sections"][0]["items"][0]
    assert [r["id"] for r in top_items_summary(doc, 2)] == ["a", "b"]
    assert top_items_summary({}) == []
    # The same lines the Markdown export writes, before this summary existed.
    assert _md_top(doc) == [
        "",
        "## Do these first",
        "",
        "1. Security: Rotate `KEY` first. (`s.py:4` and 3 more)",
        "2. Code health: Split it.",
        "3. docs:  (`d.md:2`)",
    ]


async def test_a_section_the_host_never_stored_is_listed_unavailable(async_session) -> None:
    rid = await _seed(async_session)
    full = await collect_first_look(async_session, rid, now=NOW)
    report = await collect_first_look(async_session, rid, now=NOW, unavailable=HOSTED)

    assert {"performance", "security"} <= {b.key for b in full.sections}
    assert not {"performance", "security"} & {b.key for b in report.sections}
    assert report.unavailable == {**full.unavailable, **HOSTED}
    kept = [b for b in full.sections if b.key not in HOSTED]
    assert [b.to_json() for b in report.sections] == [b.to_json() for b in kept]
    # "Do these first" is picked over what is left.
    assert report.top and set(report.top) <= {i.id for b in kept for i in b.items}


async def test_the_living_report_takes_the_same_sections(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    living = await build_living(async_session, rid, None, unavailable=HOSTED)
    data = living.to_json()
    assert not {"performance", "security"} & {s["key"] for s in data["sections"]}
    assert {k: data["unavailable"][k] for k in HOSTED} == HOSTED
    listed = {i["id"] for s in data["sections"] for i in s["items"]}
    assert set(data["top"]) <= listed and set(data["prose"]["items"]) == listed

    # A report written now (no stored one yet) leaves them out as well.
    stored = await build_living(
        async_session, rid, tmp_path, store_missing=True, unavailable=HOSTED
    )
    assert stored.wrote_baseline
    assert not {"performance", "security"} & {b.key for b in stored.baseline.sections}


async def test_default_calls_are_unchanged(async_session) -> None:
    rid = await _seed(async_session)
    default = await collect_first_look(async_session, rid, now=NOW)
    assert (await collect_first_look(async_session, rid, now=NOW, unavailable=None)).to_json() == (
        default.to_json()
    )
    assert (await collect_first_look(async_session, rid, now=NOW, unavailable={})).to_json() == (
        default.to_json()
    )
    living = await build_living(async_session, rid, None)
    for flavor in FLAVORS:
        for prompts in (True, False):
            one = await render_document(
                async_session, rid, living, app_url="http://x", flavor=flavor, prompts=prompts
            )
            many = await render_documents(
                async_session, rid, living, flavors=[flavor], app_url="http://x", prompts=prompts
            )
            assert many == {flavor: one}


async def test_every_flavor_in_one_pass_reads_the_store_once(async_session, monkeypatch) -> None:
    rid = await _seed(async_session)
    living = await build_living(async_session, rid, None)
    singles = {f: await render_document(async_session, rid, living, flavor=f) for f in FLAVORS}

    reads = {"facts": 0, "perf": 0}
    load_facts, get_perf = (
        actions_crud.load_repo_facts,
        performance_crud.get_performance_opportunity,
    )

    async def counted_facts(*a, **kw):
        reads["facts"] += 1
        return await load_facts(*a, **kw)

    async def counted_perf(*a, **kw):
        reads["perf"] += 1
        return await get_perf(*a, **kw)

    monkeypatch.setattr(actions_crud, "load_repo_facts", counted_facts)
    monkeypatch.setattr(performance_crud, "get_performance_opportunity", counted_perf)
    docs = await render_documents(async_session, rid, living, flavors=[*FLAVORS, "generic"])

    assert docs == singles and list(docs) == list(FLAVORS)
    perf_items = [i for i in living.report.items() if i.prompt.renderer == "perf_opportunity"]
    assert perf_items
    assert reads == {"facts": 1, "perf": len(perf_items)}
    assert len({d["prompts"][perf_items[0].id] for d in docs.values()}) == len(FLAVORS)


async def test_a_stored_reports_items_in_those_sections_are_left_out(
    async_session, tmp_path
) -> None:
    rid = await _seed(async_session)
    first = await build_living(async_session, rid, tmp_path, store_missing=True)
    assert {"performance", "security"} <= {b.key for b in first.baseline.sections}

    living = await build_living(async_session, rid, tmp_path, unavailable=HOSTED)
    assert living.baseline is not None and not living.wrote_baseline
    assert not {"performance", "security"} & {b.key for b in living.report.sections}
    assert not living.carried and not living.resolved
    assert {i.section for i in living.report.items()}.isdisjoint(HOSTED)
