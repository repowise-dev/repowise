"""The living report over a real store: statuses from the mark stores, new and
resolved items against the stored report, and the exports that print it."""

from __future__ import annotations

import dataclasses

from sqlalchemy import delete

from repowise.core.persistence.models import (
    DocDriftFinding,
    GitCommitFile,
    PerformanceOpportunity,
)
from repowise.core.report.first_look.living import build_living
from repowise.core.report.first_look.marks import mark_item
from repowise.core.report.first_look.render import render_first_look_markdown
from repowise.core.report.first_look.store import read_report_file, write_report_file
from repowise.core.report.first_look.write import render_document
from tests.unit.persistence.test_first_look_collect import SECRET, _seed


def _items(data: dict) -> dict[str, dict]:
    return {i["id"]: i for s in data["sections"] for i in s["items"]}


def _rule(data: dict, rule: str) -> dict:
    return next(i for i in _items(data).values() if i["rule"] == rule)


async def test_with_no_stored_report_it_writes_one_and_reads_all_open(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    living = await build_living(async_session, rid, tmp_path, store_missing=True)

    assert living.wrote_baseline and read_report_file(tmp_path) is not None
    data = living.to_json()
    assert {i["status"] for i in _items(data).values()} == {"open"}
    assert {i["change"] for i in _items(data).values()} == {None}
    assert data["baseline"] == {"stamp": living.baseline.stamp.to_json(), "resolved": []}
    for item in _items(data).values():
        assert {"status_reason", "status_until"} <= set(item)


async def test_statuses_new_and_resolved_against_the_stored_report(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    first = (await build_living(async_session, rid, tmp_path, store_missing=True)).to_json()
    secret, exports = _rule(first, "hardcoded_password"), _rule(first, "safe_to_delete")
    perf = _rule(first, "io_in_loop")

    await mark_item(async_session, rid, secret["id"], "done")
    await mark_item(async_session, rid, exports["id"], "ack", reason="plugin API")
    async_session.add(
        DocDriftFinding(repository_id=rid, file_path="docs/other.md", kind="path", line_number=2,
                        target="src/old.py", raw="src/old.py", confidence=0.9)
    )
    await async_session.execute(
        delete(PerformanceOpportunity).where(PerformanceOpportunity.opportunity_id == "perf1")
    )
    await async_session.commit()

    living = await build_living(async_session, rid, tmp_path)
    data = living.to_json()
    items = _items(data)
    assert not living.wrote_baseline
    assert items[secret["id"]]["status"] == "done"
    # The answered rows left the collector's open set; the item stays, answered.
    assert (items[exports["id"]]["status"], items[exports["id"]]["status_reason"]) == (
        "acknowledged",
        "plugin API",
    )
    # The index no longer finds it; it stays listed for the answer.
    assert items[exports["id"]]["change"] == "resolved"
    new = [i for i in items.values() if i["change"] == "new"]
    assert [i["locations"][0]["path"] for i in new] == ["docs/other.md"]
    (resolved,) = data["baseline"]["resolved"]
    assert resolved["id"] == perf["id"] and resolved["status"] == "resolved"
    assert resolved["change"] == "resolved"
    assert resolved["text"] == first["prose"]["items"][perf["id"]]
    assert perf["id"] not in items
    # "Do these first" and the whole-report prompt take open items only.
    assert secret["id"] not in data["top"] and exports["id"] not in data["top"]
    doc = await render_document(async_session, rid, living, prompts=False)
    assert "prompts" not in doc
    assert secret["id"] not in doc["report_prompt"] and new[0]["id"] in doc["report_prompt"]
    assert "repowise mark <id> done" in doc["report_prompt"]

    md = render_first_look_markdown(doc)
    assert "<details>" not in md
    assert "acknowledged: plugin API" in md and "  done · " in md
    assert "## Resolved since the first look" in md
    assert "1 resolved, 1 done, 1 acknowledged, 1 new." in md
    assert SECRET not in md


async def test_a_models_text_is_kept_where_its_facts_still_hold(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    first = await build_living(async_session, rid, tmp_path, store_missing=True)
    report = first.baseline
    prose = {
        **report.prose,
        "writer": "model-x",
        "items": {i.id: f"Model text for {i.rule}." for i in report.items()},
        "summary": "Model summary.",
    }
    stamp = dataclasses.replace(report.stamp, generator="model-x", cost=0.003)
    write_report_file(tmp_path, dataclasses.replace(report, prose=prose, stamp=stamp))
    async_session.add_all(
        [
            DocDriftFinding(repository_id=rid, file_path="docs/guide.md", kind="path",
                            line_number=9, target="src/gone.py", raw="src/gone.py", confidence=0.9),
            GitCommitFile(repository_id=rid, sha="old", file_path="src/gone.py"),
        ]
    )
    await async_session.commit()

    living = await build_living(async_session, rid, tmp_path)
    texts = living.report.prose["items"]
    docs = next(i for i in living.report.items() if i.section == "docs")
    secret = next(i for i in living.report.items() if i.section == "security")
    assert texts[secret.id] == "Model text for hardcoded_password."
    assert texts[docs.id] != "Model text for broken_doc_refs."  # its facts changed
    assert living.report.prose["summary"] != "Model summary."  # the report changed
    assert living.report.stamp.generator == "model-x"
    data = living.to_json()
    assert _items(data)[docs.id]["change"] == "changed"
    assert data["prose_source"] == {
        "writer": "model-x", "written_at": report.stamp.created_at, "cost_usd": 0.003,
    }
