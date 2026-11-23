"""write_first_look over a real store: files, row, prose, and no secret anywhere."""

from __future__ import annotations

import json
import re

from sqlalchemy import select

from repowise.core.persistence.models import StoredReport
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.report.first_look.config import FirstLookSettings
from repowise.core.report.first_look.model import FirstLookReport
from repowise.core.report.first_look.store import report_path
from repowise.core.report.first_look.write import rendered_path, write_first_look
from tests.unit.persistence.test_first_look_collect import SECRET, _seed


class EchoProvider:
    """Answers the first-look call with one plain sentence per id it was sent."""

    provider_name = "echo"
    model_name = "claude-haiku-5-5"

    def __init__(self, leak: str | None = None) -> None:
        self.calls = 0
        self.leak = leak
        self._cost_tracker = None

    def supported_reasoning_modes(self) -> tuple[str, ...]:
        return ("auto",)

    async def generate(self, system_prompt: str, user_prompt: str, **kw) -> GeneratedResponse:
        self.calls += 1
        sent = json.loads(user_prompt)
        text = f'It reads password = "{self.leak}".' if self.leak else "Fix this one first."
        reply = {
            "summary": "Start with the security item.",
            "sections": [
                {
                    "key": s["key"],
                    "intro": "Read this section next.",
                    "items": [{"id": i["id"], "text": text} for i in s["items"]],
                }
                for s in sent["sections"]
            ],
        }
        return GeneratedResponse(json.dumps(reply), 3_000, 800, stop_reason="end_turn")


async def test_writes_json_markdown_html_and_the_row(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    # A provider that cannot answer leaves the template in place.
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


async def test_a_provider_writes_the_prose_in_one_call(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    provider = EchoProvider()
    report = await write_first_look(async_session, rid, tmp_path, provider=provider)
    assert provider.calls == 1
    assert report.stamp.generator == "claude-haiku-5-5"
    assert report.stamp.cost == (3_000 * 0.10 + 800 * 0.50) / 1e6
    assert report.prose["writer"] == "claude-haiku-5-5"
    assert report.prose["summary"] == "Start with the security item."
    assert set(report.prose["items"].values()) == {"Fix this one first."}
    md = rendered_path(tmp_path, ".md").read_text(encoding="utf-8")
    assert "Fix this one first." in md and "src/settings.py:4" in md


async def test_model_text_holding_a_secret_never_reaches_a_file(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    report = await write_first_look(
        async_session, rid, tmp_path, provider=EchoProvider(leak="Gx7pQ2vLm9Rt4")
    )
    assert report.stamp.generator == "claude-haiku-5-5"  # the intros and summary were used
    for path in (report_path(tmp_path), rendered_path(tmp_path, ".md"), rendered_path(tmp_path, ".html")):
        assert "Gx7pQ2vLm9Rt4" not in path.read_text(encoding="utf-8")


async def test_the_llm_setting_off_makes_no_call(async_session, tmp_path) -> None:
    rid = await _seed(async_session)
    provider = EchoProvider()
    report = await write_first_look(
        async_session, rid, tmp_path, provider=provider, settings=FirstLookSettings(llm=False)
    )
    assert provider.calls == 0 and report.stamp.generator == "template"


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
