"""The first-look report's one model call: input, guards, fallbacks, spend."""

from __future__ import annotations

import json
from typing import Any

import pytest

from repowise.core.analysis.security.credentials import holds_secret
from repowise.core.cost_estimator import estimate_first_look_cost
from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    record_generation_cost,
)
from repowise.core.report.first_look.llm import (
    MAX_TOKENS,
    OPERATION,
    build_input,
    clean_text,
    first_look_model,
    rewrite_prose,
)
from repowise.core.report.first_look.model import (
    FirstLookReport,
    Item,
    Location,
    PromptRef,
    SectionBlock,
    Stamp,
    file_link,
    item_id,
)

_AWS = "AKIA" + "QZXNRTVYWMPKLBHG"
_PASSWORD_LINE = 'password = "' + "Tr0ub4dor&3xq" + '"'


def _item(section: str, rule: str, path: str, facts: dict[str, Any]) -> Item:
    return Item(
        id=item_id(section, rule, path),
        section=section,
        rule=rule,
        title_facts=facts,
        locations=(Location(path, 3),),
        confidence="high",
        basis="measured",
        severity="act_now",
        effort="S",
        link=file_link(path, 3),
        prompt=PromptRef("first_look_item"),
        fingerprint="f",
    )


SECRET = _item("security", "hardcoded_password", "backend/scripts/seed_admin.py", {"locations": 1})
SLOW = _item(
    "performance",
    "io_in_loop",
    "backend/services/billing.py",
    {"symbol": "sync_all", "call_sites": 2, "lift": 8.28},
)
DOCS = _item(
    "docs",
    "broken_doc_refs",
    "docs/guide.md",
    {"references": 2, "targets": ["backend/OLD_NOTES.md"], "verify": "pytest -q"},
)


def _report() -> FirstLookReport:
    return FirstLookReport(
        stamp=Stamp("c1203324aaaa", None, "2026-11-20T00:00:00Z"),
        repo={"name": "demo", "files": 120, "health": {"score": 7.7, "band": "good"}},
        sections=(
            SectionBlock("security", (SECRET,)),
            SectionBlock("performance", (SLOW,), {"production_causes": 235}),
            SectionBlock("docs", (DOCS,)),
        ),
        top=(SECRET.id, SLOW.id),
    )


TEMPLATE = {
    "writer": "template",
    "summary": "TEMPLATE summary",
    "sections": {"security": "T sec", "performance": "T perf", "docs": "T docs"},
    "items": {SECRET.id: "T secret", SLOW.id: "T slow", DOCS.id: "T docs item"},
    "setup": {"coverage": "T setup"},
}


def _reply(**over: Any) -> dict[str, Any]:
    texts = {
        SECRET.id: "seed_admin.py has a password in it. Rotate it first.",
        SLOW.id: "sync_all reads in a loop from 2 call sites. Batch the reads.",
        DOCS.id: "A guide points at OLD_NOTES.md, which is gone. Fix the 2 references.",
    }
    texts.update(over.pop("items", {}))
    reply = {
        "summary": "Rotate one password, then batch the reads in sync_all.",
        "sections": [
            {"key": key, "intro": f"Intro for {key}.", "items": [{"id": i, "text": texts[i]}]}
            for key, i in (("security", SECRET.id), ("performance", SLOW.id), ("docs", DOCS.id))
        ],
    }
    reply.update(over)
    return reply


class FakeProvider:
    provider_name = "fake"
    model_name = "claude-haiku-5-5"

    def __init__(self, content: str | Exception, *, stop_reason: str = "end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []
        self._cost_tracker = None

    def supported_reasoning_modes(self) -> tuple[str, ...]:
        return ("auto", "low")

    async def generate(self, system_prompt: str, user_prompt: str, **kw: Any) -> GeneratedResponse:
        self.calls.append({"system": system_prompt, "user": user_prompt, **kw})
        if isinstance(self.content, Exception):
            raise self.content
        response = GeneratedResponse(
            content=self.content,
            input_tokens=4_000,
            output_tokens=1_000,
            stop_reason=self.stop_reason,
        )
        # As every real provider does: the spend goes on the attached tracker.
        await record_generation_cost(self._cost_tracker, model=self.model_name, result=response)
        return response


async def _run(content: str | Exception, **kw: Any):
    provider = FakeProvider(content, **kw)
    prose, outcome = await rewrite_prose(_report(), provider, template=TEMPLATE)
    assert len(provider.calls) == 1  # one call, never retried
    return prose, outcome, provider


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def test_input_holds_facts_and_basenames_only() -> None:
    sent = build_input(_report())
    blob = json.dumps(sent)
    assert "backend/" not in blob  # no full path reaches the model
    assert "seed_admin.py" in blob and "OLD_NOTES.md" in blob
    assert "verify" not in blob and "pytest" not in blob  # commands come from ids
    docs = sent["sections"][2]["items"][0]
    assert docs["facts"]["targets"] == ["OLD_NOTES.md"]
    assert sent["top"] == [SECRET.id, SLOW.id]


def test_input_drops_names_of_people() -> None:
    item = _item("ownership", "bus_factor", "a.py", {"files": 3, "author": "Jane", "owners": 1})
    report = FirstLookReport(Stamp(None, None, "t"), {}, (SectionBlock("ownership", (item,)),))
    facts = build_input(report)["sections"][0]["items"][0]["facts"]
    assert facts == {"files": 3, "owners": 1}


# ---------------------------------------------------------------------------
# Guards, one at a time
# ---------------------------------------------------------------------------


async def test_valid_reply_is_used_whole() -> None:
    prose, outcome, provider = await _run(json.dumps(_reply()))
    assert outcome.used and outcome.fallbacks == {}
    assert prose["writer"] == "claude-haiku-5-5"
    assert prose["items"][SLOW.id].startswith("sync_all reads")
    assert prose["sections"]["docs"] == "Intro for docs."
    assert prose["setup"] == TEMPLATE["setup"]
    assert TEMPLATE["writer"] == "template"  # the caller's template is not mutated
    assert outcome.input_tokens == 4_000 and outcome.output_tokens == 1_000
    assert outcome.cost_usd == pytest.approx((4_000 * 0.10 + 1_000 * 0.50) / 1e6)
    call = provider.calls[0]
    assert call["max_tokens"] == MAX_TOKENS and call["reasoning"] == "low"


async def test_bad_json_keeps_the_whole_template() -> None:
    prose, outcome, _ = await _run("Here is your report: it went well!")
    assert prose == TEMPLATE
    assert outcome.fallback_reason == "unparseable" and not outcome.used
    assert outcome.input_tokens == 4_000  # the spend is still reported


async def test_wrong_shape_keeps_the_whole_template() -> None:
    prose, outcome, _ = await _run(json.dumps({"summary": "x", "sections": "nope"}))
    assert prose == TEMPLATE and outcome.fallback_reason == "unparseable"


async def test_provider_error_keeps_the_template_and_is_not_retried() -> None:
    prose, outcome, _ = await _run(ProviderError("fake", "boom"))
    assert prose == TEMPLATE
    assert outcome.fallback_reason == "provider_error" and outcome.cost_usd is None


async def test_unknown_ids_are_dropped() -> None:
    reply = _reply()
    reply["sections"][0]["items"].append({"id": "fl_000000000000", "text": "Invented item."})
    prose, outcome, _ = await _run(json.dumps(reply))
    assert "fl_000000000000" not in prose["items"]
    assert outcome.unknown_ids == 1 and outcome.used


async def test_missing_id_keeps_its_template_text() -> None:
    reply = _reply()
    reply["sections"][1]["items"] = []
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["items"][SLOW.id] == "T slow"
    assert outcome.fallbacks == {SLOW.id: "missing"} and outcome.item_fallbacks == 1


async def test_invented_number_keeps_the_template() -> None:
    reply = _reply(items={SLOW.id: "sync_all runs 40 queries per request."})
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["items"][SLOW.id] == "T slow"
    assert outcome.fallbacks == {SLOW.id: "number"}


async def test_numbers_from_the_facts_pass_rounded() -> None:
    reply = _reply(items={SLOW.id: "sync_all is 8.3x slower from 2 call sites."})
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["items"][SLOW.id] == "sync_all is 8.3x slower from 2 call sites."
    assert outcome.fallbacks == {}


async def test_intro_numbers_come_from_its_section() -> None:
    reply = _reply()
    reply["sections"][1]["intro"] = "235 places in production code do slow work."
    reply["sections"][2]["intro"] = "999 docs are broken."
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["sections"]["performance"].startswith("235 places")
    assert prose["sections"]["docs"] == "T docs"
    assert outcome.fallbacks == {"section:docs": "number"} and outcome.item_fallbacks == 0


async def test_invented_path_keeps_the_template() -> None:
    reply = _reply(items={SECRET.id: "Move the password into config/settings.py today."})
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["items"][SECRET.id] == "T secret"
    assert outcome.fallbacks == {SECRET.id: "path"}


async def test_a_path_the_item_holds_passes() -> None:
    text = "backend/scripts/seed_admin.py has a password. Rotate it."
    prose, outcome, _ = await _run(json.dumps(_reply(items={SECRET.id: text})))
    assert prose["items"][SECRET.id] == text and outcome.fallbacks == {}


async def test_markdown_links_urls_code_and_html_are_stripped() -> None:
    text = (
        "Read [the guide](https://evil.example/x) at https://evil.example/y, "
        "then run `make` <img src=x onerror=alert(1)> **now** — quickly."
    )
    prose, outcome, _ = await _run(json.dumps(_reply(items={SLOW.id: text})))
    out = prose["items"][SLOW.id]
    assert out == "Read the guide at, then run make now, quickly."
    assert "http" not in out and "<" not in out and "](" not in out
    assert outcome.fallbacks == {}


def test_markdown_block_marks_are_dropped() -> None:
    assert clean_text("# Fix it now") == "Fix it now"
    assert clean_text("- Fix *this* | that") == "Fix this that"
    assert clean_text("Keep the .env file out") == "Keep the .env file out"


@pytest.mark.parametrize("text", ["Fix 3-5000 things.", "Split it 1/9999 ways.", "Run .75 times."])
async def test_every_digit_run_is_checked(text: str) -> None:
    _, outcome, _ = await _run(json.dumps(_reply(items={SLOW.id: text})))
    assert outcome.fallbacks == {SLOW.id: "number"}


async def test_an_invented_email_keeps_the_template() -> None:
    _, outcome, _ = await _run(json.dumps(_reply(items={SLOW.id: "Ask x@y.io about sync_all."})))
    assert outcome.fallbacks == {SLOW.id: "path"}


def test_paths_inside_sentences_reach_the_model_as_basenames() -> None:
    item = _item("health", "finding", "src/app/foo.py", {"title": "Split src/app/foo.py now"})
    report = FirstLookReport(Stamp(None, None, "t"), {}, (SectionBlock("health", (item,)),))
    facts = build_input(report)["sections"][0]["items"][0]["facts"]
    assert facts == {"title": "Split foo.py now"}


def test_holds_secret_uses_the_scan_detectors() -> None:
    assert holds_secret(f"key {_AWS}")
    assert holds_secret(_PASSWORD_LINE)
    assert not holds_secret(
        "Rotate the password in seed_admin.py, then read it from the environment."
    )


def test_a_bare_domain_reads_as_a_path() -> None:
    assert clean_text("see evil.example") == "see evil.example"  # left to the path guard


async def test_bare_domain_fails_the_path_guard() -> None:
    reply = _reply(items={SLOW.id: "Report it on evil.example today."})
    _, outcome, _ = await _run(json.dumps(reply))
    assert outcome.fallbacks == {SLOW.id: "path"}


@pytest.mark.parametrize("secret", [f"The key {_AWS} is live.", f"It reads {_PASSWORD_LINE}."])
async def test_secret_shaped_text_keeps_the_template(secret: str) -> None:
    prose, outcome, _ = await _run(json.dumps(_reply(items={SECRET.id: secret})))
    assert prose["items"][SECRET.id] == "T secret"
    assert outcome.fallbacks == {SECRET.id: "secret"}
    assert _AWS not in json.dumps(prose)


async def test_secret_in_the_summary_keeps_its_template() -> None:
    prose, outcome, _ = await _run(json.dumps(_reply(summary=f"Rotate {_AWS} now.")))
    assert prose["summary"] == "TEMPLATE summary"
    assert outcome.fallbacks == {"summary": "secret"}


async def test_truncated_reply_keeps_complete_items_and_templates_the_rest() -> None:
    full = json.dumps(_reply())
    cut = full[: full.index(DOCS.id) + 20]  # stops inside the docs item
    prose, outcome, _ = await _run(cut, stop_reason="max_tokens")
    assert outcome.truncated and outcome.used
    assert prose["items"][SECRET.id].startswith("seed_admin.py")
    assert prose["items"][SLOW.id].startswith("sync_all")
    assert prose["items"][DOCS.id] == "T docs item"
    assert prose["summary"].startswith("Rotate one password")  # written before the cut
    assert prose["sections"]["docs"] == "Intro for docs."
    assert outcome.fallbacks == {DOCS.id: "truncated"}


async def test_truncated_with_nothing_complete_keeps_the_whole_template() -> None:
    prose, outcome, _ = await _run('{"sections": [{"ke', stop_reason="max_tokens")
    assert prose == TEMPLATE and outcome.fallback_reason == "truncated"


async def test_nothing_usable_keeps_the_template_writer() -> None:
    reply = {"summary": 7, "sections": [{"key": "security", "items": []}]}
    prose, outcome, _ = await _run(json.dumps(reply))
    assert prose["writer"] == "template"
    assert outcome.fallback_reason == "no_usable_text"


# ---------------------------------------------------------------------------
# Spend, model and cost plan
# ---------------------------------------------------------------------------


async def test_spend_is_recorded_under_the_first_look_operation() -> None:
    from repowise.core.generation.cost_tracker import CostTracker

    provider = FakeProvider(json.dumps(_reply()))
    # Buffered rows stay in memory until a flush, so the label can be read back.
    tracker = CostTracker(session_factory=object(), repo_id="r1", buffered=True)
    provider._cost_tracker = tracker
    await rewrite_prose(_report(), provider, template=TEMPLATE)
    assert [row["operation"] for row in tracker._pending] == [OPERATION]
    assert tracker.operation == "doc_generation"  # restored after the call


async def test_model_override_that_cannot_be_built_falls_back_without_a_call() -> None:
    provider = FakeProvider(json.dumps(_reply()))
    prose, outcome = await rewrite_prose(
        _report(), provider, template=TEMPLATE, model="no-such-model"
    )
    assert provider.calls == [] and prose == TEMPLATE
    assert outcome.fallback_reason == "provider_error" and outcome.model == "no-such-model"


def test_model_choice() -> None:
    provider = FakeProvider("")
    assert first_look_model(provider) == "claude-haiku-5-5"
    assert first_look_model(provider, "") == "claude-haiku-5-5"
    assert first_look_model(provider, "claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_cost_plan_is_one_fixed_call() -> None:
    estimate = estimate_first_look_cost("anthropic", "claude-haiku-5-5")
    assert estimate.total_pages == 1
    assert 0 < estimate.estimated_cost_usd < 0.01
    sonnet = estimate_first_look_cost("anthropic", "claude-sonnet-5-5")
    assert sonnet.estimated_cost_usd > estimate.estimated_cost_usd
