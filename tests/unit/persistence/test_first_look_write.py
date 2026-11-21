"""write_first_look over a real store: files, row, prose, and no secret anywhere."""

from __future__ import annotations

import json
import re

from sqlalchemy import select

from repowise.core.persistence.models import StoredReport
from repowise.core.report.first_look.config import FirstLookSettings
from repowise.core.report.first_look.model import FirstLookReport
from repowise.core.report.first_look.store import report_path
from repowise.core.report.first_look.write import rendered_path, write_first_look
from tests.unit.persistence.test_first_look_collect import SECRET, _seed


async def test_writes_json_markdown_html_and_the_row(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    # A provider is accepted and not used yet: the template writes every report.
    report = await write_first_look(async_session, rid, tmp_path, provider=object())

    assert report.stamp.generator == "template"
    assert report.prose["writer"] == "template"
    assert set(report.prose["items"]) == {i.id for i in report.items()}
    stored = json.loads(report_path(tmp_path).read_text(encoding="utf-8"))
    assert FirstLookReport.from_json(stored).prose == report.prose
    rows = (await async_session.execute(select(StoredReport))).scalars().all()
    assert len(rows) == 1 and json.loads(rows[0].payload_json)["prose"] == report.prose

    md = rendered_path(tmp_path, ".md").read_text(encoding="utf-8")
    html = rendered_path(tmp_path, ".html").read_text(encoding="utf-8")
    listed = sum(min(len(s.items), 12) for s in report.sections)
    assert "<details>" not in md
    assert html.count("<summary>Fix with your agent</summary>") == listed
    assert "src/settings.py:4" in md and "values redacted" in md
    assert "No live secrets" not in md
    assert html.startswith("<!doctype html>") and "src/settings.py" in html
    assert "—" not in md + html
    for text in (md, html, json.dumps(stored)):
        assert SECRET not in text


async def test_the_setting_off_writes_nothing(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    report = await write_first_look(
        async_session, rid, tmp_path, settings=FirstLookSettings(enabled=False)
    )
    assert report.items()
    assert not (tmp_path / ".repowise").exists()
    assert (await async_session.execute(select(StoredReport))).first() is None


async def test_links_point_into_a_running_app(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    await write_first_look(async_session, rid, tmp_path, app_url="http://127.0.0.1:3000/")
    md = rendered_path(tmp_path, ".md").read_text(encoding="utf-8")
    assert f"(http://127.0.0.1:3000/repos/{rid}/files/src/settings.py?line=4)" in md
    assert not re.search(r"\]\(/", md)
