"""Broad session discovery: packet bounds, grounding, and the one-call rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.discovery import (
    ProseSpan,
    build_packet,
    ground_candidates,
    parse_response,
    run_update_discovery,
)
from repowise.core.analysis.decisions.discovery.runner import candidate_hash
from repowise.core.analysis.decisions.policy import preset_policy, resolve_policy
from repowise.core.sessions.staging import DISCOVERY_KIND, MAX_SPAN_ATTEMPTS, SessionStagingStore

QUOTE = "Never run ruff format on this repository; CI diffs the formatting."
PROSE = (
    "Do not run ruff format here. " + QUOTE + " We settled that after the "
    "repo-wide reformat blew up every open branch."
)


def _span(index: int, session: str = "s1", text: str = PROSE, files=("packages/core/a.py",)):
    return ProseSpan(
        span_id=f"span{index:02d}",
        session_id=session,
        role="user",
        text=text,
        files=tuple(files),
        ts=float(index),
    )


def _candidate(**overrides):
    item = {
        "title": "Never run ruff format",
        "decision": "Never run ruff format on this repository because CI diffs the formatting.",
        "rationale": "CI diffs the formatting.",
        "kind": "constraint",
        "durability": "durable",
        "acceptance_basis": "user_explicit",
        "evidence_quote": QUOTE,
        "span_ids": ["span01"],
        "paths": ["packages/core/a.py"],
    }
    item.update(overrides)
    return item


def _payload(*items):
    return {
        "candidates": list(items),
        "rejected_task_local": 0,
        "rejected_assistant_only": 0,
    }


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content
        self.input_tokens = 1234
        self.output_tokens = 56


class FakeProvider:
    """Counts calls, so the one-call invariant is an assertion not a promise."""

    def __init__(self, content: str = "", *, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls = 0

    async def generate(self, system, prompt, **kwargs):
        self.calls += 1
        self.prompt = prompt
        if self.error is not None:
            raise self.error
        return _Response(self.content)


class ExplodingProvider:
    """Any call at all is the failure."""

    async def generate(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("a model call was made on a no-call path")


# ---------------------------------------------------------------------------
# Packet
# ---------------------------------------------------------------------------


def test_packet_admits_sessions_whole_and_in_queue_order():
    spans = [_span(1, "s1"), _span(2, "s1"), _span(3, "s2")]
    packet = build_packet(spans, max_sessions=2, max_input_tokens=30_000)
    assert packet.sessions == ("s1", "s2")
    assert [s.span_id for s in packet.spans] == ["span01", "span02", "span03"]


def test_packet_stops_at_the_session_bound_and_leaves_the_rest_queued():
    spans = [_span(1, "s1"), _span(2, "s2"), _span(3, "s3")]
    packet = build_packet(spans, max_sessions=1, max_input_tokens=30_000)
    assert packet.sessions == ("s1",)
    assert len(packet.spans) == 1


def test_packet_stops_at_the_token_bound():
    # Two sessions of ~1,300 tokens each against a budget that fits one.
    big = "word " * 800
    spans = [_span(1, "s1", text=big), _span(2, "s2", text=big)]
    packet = build_packet(spans, max_sessions=12, max_input_tokens=3_000)
    assert packet.sessions == ("s1",)


def test_one_oversized_session_never_wedges_the_queue():
    """Trimming stops at one span: refusing to send it would drain nothing."""
    huge = "word " * 20_000
    spans = [_span(1, "s1", text=huge), _span(2, "s1", text=huge)]
    packet = build_packet(spans, max_sessions=12, max_input_tokens=4_000)
    assert packet.sessions == ("s1",)
    assert len(packet.spans) == 1


def test_packet_offers_only_the_files_its_spans_touched():
    packet = build_packet([_span(1)], max_sessions=12, max_input_tokens=30_000)
    assert packet.known_paths == ("packages/core/a.py",)
    assert "packages/core/a.py" in packet.prompt
    assert "span01" in packet.prompt


def test_empty_queue_builds_no_packet():
    assert not build_packet([], max_sessions=12, max_input_tokens=30_000)


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def test_a_grounded_candidate_survives():
    result = ground_candidates(_payload(_candidate()), (_span(1),))
    assert len(result.grounded) == 1
    candidate = result.grounded[0]
    assert candidate.verification == "exact"
    assert candidate.affected_files == ("packages/core/a.py",)
    assert candidate.to_structured()["discovery"] is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"title": ""}, "empty_claim"),
        ({"decision": "add tests"}, "generic_claim"),
        ({"durability": "task_local"}, "task_local"),
        ({"span_ids": ["nope"]}, "unknown_span"),
        ({"span_ids": ["span01", "nope"]}, "unknown_span"),
        ({"span_ids": []}, "unknown_span"),
        ({"evidence_quote": "We agreed to migrate everything to Bazel."}, "unverified_quote"),
        (
            {"decision": "Delete the entire persistence layer and rewrite it in Rust today."},
            "ungrounded_claim",
        ),
    ],
)
def test_ungrounded_candidates_are_rejected_with_a_reason(overrides, reason):
    result = ground_candidates(_payload(_candidate(**overrides)), (_span(1),))
    assert result.grounded == []
    assert result.rejected == {reason: 1}


def test_an_invented_path_never_becomes_scope():
    """Unresolved beats guessed: a repository-wide claim is not pinned anywhere."""
    result = ground_candidates(
        _payload(_candidate(paths=["packages/web/src/invented.tsx"])), (_span(1),)
    )
    assert result.grounded[0].affected_files == ()


def test_a_selected_path_from_the_known_set_becomes_scope():
    result = ground_candidates(_payload(_candidate()), (_span(1),))
    assert result.grounded[0].affected_files == ("packages/core/a.py",)


def test_the_packet_never_exceeds_the_token_ceiling():
    spans = [_span(i, f"s{i}", text="word " * 900) for i in range(1, 30)]
    packet = build_packet(spans, max_sessions=24, max_input_tokens=8_000)
    assert 0 < packet.estimated_tokens <= 8_000


def test_an_invented_rationale_is_dropped_without_killing_the_candidate():
    result = ground_candidates(
        _payload(_candidate(rationale="Because the board mandated a Kubernetes migration.")),
        (_span(1),),
    )
    assert result.grounded[0].rationale == ""


def test_a_combined_claim_is_flagged_never_split():
    result = ground_candidates(
        _payload(
            _candidate(
                decision=(
                    "Never run ruff format on this repository; "
                    "CI diffs the formatting on every branch."
                )
            )
        ),
        (_span(1),),
    )
    assert len(result.grounded) == 1
    assert result.grounded[0].needs_split is True


@pytest.mark.parametrize("content", ["", "not json at all", '{"candidates": "nope"}', "[]"])
def test_malformed_output_grounds_nothing(content):
    result = ground_candidates(parse_response(content), (_span(1),))
    assert result.grounded == []
    assert result.rejected.get("malformed") == 1


def test_fenced_json_still_parses():
    body = "```json\n" + json.dumps(_payload(_candidate())) + "\n```"
    assert ground_candidates(parse_response(body), (_span(1),)).grounded


def test_identity_covers_claim_and_evidence():
    one = ground_candidates(_payload(_candidate()), (_span(1),)).grounded[0]
    same = ground_candidates(_payload(_candidate()), (_span(1),)).grounded[0]
    assert candidate_hash(one) == candidate_hash(same)


# ---------------------------------------------------------------------------
# Runner: the one-call invariant and the backlog
# ---------------------------------------------------------------------------


def _queue(repo: Path, spans: list[ProseSpan], now: float = 100.0) -> None:
    with SessionStagingStore.open_default(repo) as store:
        for span in spans:
            store.add_discovery_span(
                span_id=span.span_id,
                session_id=span.session_id,
                role=span.role,
                text=span.text,
                files=list(span.files),
                ts=span.ts,
                now=now,
            )
        store.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (resolve_policy(None).policy, "switched off"),
        (preset_policy("off"), "capture is off"),
        (preset_policy("full").with_llm(False), "LLM extraction is off"),
    ],
)
async def test_disabled_paths_make_no_model_call(tmp_path, policy, reason):
    """Each guard names the switch that stopped it, not a generic zero."""
    _queue(tmp_path, [_span(1)])
    outcome = await run_update_discovery(
        tmp_path, provider=ExplodingProvider(), policy=policy, now=200.0
    )
    assert outcome.report.status == "not_run"
    assert outcome.report.calls == 0
    assert reason in outcome.report.reason


@pytest.mark.asyncio
async def test_no_provider_makes_no_call_and_says_so(tmp_path):
    _queue(tmp_path, [_span(1)])
    outcome = await run_update_discovery(
        tmp_path, provider=None, policy=preset_policy("balanced"), now=200.0
    )
    assert outcome.report.status == "skipped_no_provider"
    assert outcome.report.calls == 0


@pytest.mark.asyncio
async def test_empty_input_makes_no_call(tmp_path):
    outcome = await run_update_discovery(
        tmp_path, provider=ExplodingProvider(), policy=preset_policy("balanced"), now=200.0
    )
    assert outcome.report.status == "empty"
    assert outcome.report.calls == 0


@pytest.mark.asyncio
async def test_a_large_backlog_still_costs_exactly_one_call(tmp_path):
    _queue(tmp_path, [_span(i, f"s{i}") for i in range(1, 40)])
    provider = FakeProvider(json.dumps(_payload()))
    outcome = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=200.0
    )
    assert provider.calls == 1
    assert outcome.report.calls == 1
    assert outcome.report.sessions_considered == 12
    assert outcome.report.spans_deferred == 39 - 12


@pytest.mark.asyncio
async def test_overflow_is_queued_oldest_first_not_dropped(tmp_path):
    _queue(tmp_path, [_span(i, f"s{i}") for i in range(1, 16)])
    policy = preset_policy("balanced").with_discovery(max_sessions=2)
    provider = FakeProvider(json.dumps(_payload()))
    await run_update_discovery(tmp_path, provider=provider, policy=policy, now=200.0)
    assert "span01" in provider.prompt and "span02" in provider.prompt
    assert "span03" not in provider.prompt

    await run_update_discovery(tmp_path, provider=provider, policy=policy, now=300.0)
    assert "span03" in provider.prompt and "span04" in provider.prompt
    assert "span01" not in provider.prompt


@pytest.mark.asyncio
async def test_a_provider_failure_retries_the_same_spans_then_retires_them(tmp_path):
    _queue(tmp_path, [_span(1)])
    provider = FakeProvider(error=RuntimeError("rate limited"))
    for attempt in range(1, MAX_SPAN_ATTEMPTS + 1):
        outcome = await run_update_discovery(
            tmp_path, provider=provider, policy=preset_policy("balanced"), now=200.0 + attempt
        )
        assert outcome.report.status == "failed"
        assert outcome.report.calls == 0
    assert outcome.report.spans_retired == 1
    with SessionStagingStore.open_default(tmp_path) as store:
        assert store.pending_discovery_count() == 0


@pytest.mark.asyncio
async def test_grounded_candidates_persist_and_rerunning_is_idempotent(tmp_path):
    _queue(tmp_path, [_span(1)])
    provider = FakeProvider(json.dumps(_payload(_candidate())))
    first = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=200.0
    )
    assert first.report.candidates_grounded == 1
    assert first.report.candidates_new == 1
    # Spans are spent once read, so a second update has nothing left to send.
    second = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=300.0
    )
    assert second.report.status == "empty"
    assert provider.calls == 1

    with SessionStagingStore.open_default(tmp_path) as store:
        assert store.pending_raws(10) == []  # never fed to the deterministic lane
        rows = store.promotable()
    assert rows == []  # one observation is not enough to propose anything


@pytest.mark.asyncio
async def test_a_second_session_promotes_the_candidate_as_proposed(tmp_path):
    provider = FakeProvider(json.dumps(_payload(_candidate())))
    _queue(tmp_path, [_span(1, "s1")], now=100.0)
    await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=200.0
    )
    _queue(tmp_path, [_span(2, "s2")], now=300.0)
    provider.content = json.dumps(_payload(_candidate(span_ids=["span02"])))
    outcome = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=400.0
    )
    assert outcome.decisions
    assert {d.status for d in outcome.decisions} == {"proposed"}
    assert {d.source for d in outcome.decisions} == {"session"}


@pytest.mark.asyncio
async def test_discovery_never_promotes_the_deterministic_lanes_backlog(tmp_path):
    with SessionStagingStore.open_default(tmp_path) as store:
        store.add_raw(
            hash_="det1",
            kind="user_correction",
            quotes=[QUOTE],
            files=["packages/core/a.py"],
            session_id="s9",
            now=100.0,
        )
        store.upsert_structured(
            "det1",
            kind="user_correction",
            title="A deterministic gate hit",
            structured={"decision": "Do the thing", "verification": "exact"},
            quotes=[QUOTE],
            files=["packages/core/a.py"],
            session_id="s9",
            now=100.0,
        )
        store.commit()
        assert len(store.promotable()) == 1

    _queue(tmp_path, [_span(1)])
    outcome = await run_update_discovery(
        tmp_path,
        provider=FakeProvider(json.dumps(_payload())),
        policy=preset_policy("balanced"),
        now=200.0,
    )
    assert outcome.decisions == []
    with SessionStagingStore.open_default(tmp_path) as store:
        assert [row["kind"] for row in store.promotable()] == ["user_correction"]


@pytest.mark.asyncio
async def test_a_candidate_citing_an_invented_span_is_dropped_not_stored(tmp_path):
    _queue(tmp_path, [_span(1)])
    outcome = await run_update_discovery(
        tmp_path,
        provider=FakeProvider(json.dumps(_payload(_candidate(span_ids=["ghost"])))),
        policy=preset_policy("balanced"),
        now=200.0,
    )
    assert outcome.report.candidates_returned == 1
    assert outcome.report.candidates_grounded == 0
    assert outcome.report.rejected == {"unknown_span": 1}
    with SessionStagingStore.open_default(tmp_path) as store:
        assert store.promotable() == []


def test_discovery_rows_are_hidden_from_the_deterministic_structuring_queue(tmp_path):
    with SessionStagingStore.open_default(tmp_path) as store:
        store.add_raw(
            hash_="d1",
            kind=DISCOVERY_KIND,
            quotes=[QUOTE],
            files=[],
            session_id="s1",
            now=100.0,
        )
        store.add_raw(
            hash_="c1",
            kind="explicit_choice",
            quotes=[QUOTE],
            files=[],
            session_id="s1",
            now=100.0,
        )
        store.commit()
        assert [row["hash"] for row in store.pending_raws(10)] == ["c1"]


def test_a_paraphrased_quote_is_not_evidence_in_this_lane():
    """A loose overlap across a whole transcript is not a verbatim sentence."""
    result = ground_candidates(
        _payload(
            _candidate(
                evidence_quote=(
                    "the team agreed formatting tools stay disabled across every "
                    "continuous integration pipeline"
                )
            )
        ),
        (_span(1),),
    )
    assert result.grounded == []
    assert result.rejected == {"unverified_quote": 1}


def test_a_verbatim_quote_that_only_lost_an_article_still_counts():
    """Exact-substring alone rejected 4 of 7 measured candidates over reflow."""
    result = ground_candidates(
        _payload(_candidate(evidence_quote="Never run ruff format on repository; CI diffs the formatting.")),
        (_span(1),),
    )
    assert len(result.grounded) == 1
    assert result.grounded[0].verification == "fuzzy"


def test_a_claim_carried_only_by_function_words_is_rejected():
    result = ground_candidates(
        _payload(
            _candidate(decision="Always deploy the production bundle to the Fridays release train.")
        ),
        (_span(1),),
    )
    assert result.grounded == []
    assert result.rejected == {"ungrounded_claim": 1}


def test_the_selectable_path_set_is_the_packets_resolved_one():
    packet = build_packet([_span(1)], max_sessions=12, max_input_tokens=30_000)
    result = ground_candidates(_payload(_candidate()), packet.spans, packet.known_paths)
    assert result.grounded[0].affected_files == ("packages/core/a.py",)


def test_discovery_and_deterministic_titles_never_share_a_staging_row(tmp_path):
    """A colliding title must not overwrite the other lane's row or its kind."""
    with SessionStagingStore.open_default(tmp_path) as store:
        store.add_raw(
            hash_="det1", kind="user_correction", quotes=[QUOTE], files=[],
            session_id="s9", now=100.0,
        )
        det_key = store.upsert_structured(
            "det1",
            kind="user_correction",
            title="Never run ruff format",
            structured={"decision": "the deterministic text", "verification": "exact"},
            quotes=[QUOTE],
            files=[],
            session_id="s9",
            now=100.0,
        )
        store.add_raw(
            hash_="dis1", kind=DISCOVERY_KIND, quotes=[QUOTE], files=[],
            session_id="s8", now=100.0,
        )
        dis_key = store.upsert_structured(
            "dis1",
            kind=DISCOVERY_KIND,
            title="Never run ruff format",
            structured={"decision": "the discovery text", "verification": "exact"},
            quotes=[QUOTE],
            files=[],
            session_id="s8",
            lane=DISCOVERY_KIND,
            now=100.0,
        )
        store.commit()
        assert det_key != dis_key
        rows = {row["key"]: row for row in store.promotable()}
    assert rows[det_key]["kind"] == "user_correction"
    assert rows[det_key]["structured"]["decision"] == "the deterministic text"
    assert dis_key not in rows  # one discovery observation does not promote


def test_pending_spans_are_never_pruned_before_they_are_read(tmp_path):
    _queue(tmp_path, [_span(1), _span(2)], now=100.0)
    with SessionStagingStore.open_default(tmp_path) as store:
        store.mark_discovery_consumed(["span01"], now=100.0)
        store.commit()
        store.prune(now=100.0 + 200 * 86400)
        store.commit()
        assert store.pending_discovery_count() == 1


@pytest.mark.asyncio
async def test_a_second_sighting_of_a_claim_accretes_rather_than_duplicating(tmp_path):
    provider = FakeProvider(json.dumps(_payload(_candidate())))
    _queue(tmp_path, [_span(1, "s1")], now=100.0)
    first = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=200.0
    )
    _queue(tmp_path, [_span(2, "s2")], now=300.0)
    provider.content = json.dumps(_payload(_candidate(span_ids=["span02"])))
    second = await run_update_discovery(
        tmp_path, provider=provider, policy=preset_policy("balanced"), now=400.0
    )
    assert (first.report.candidates_new, first.report.candidates_accreted) == (1, 0)
    assert (second.report.candidates_new, second.report.candidates_accreted) == (0, 1)


@pytest.mark.asyncio
async def test_an_exhausted_span_does_not_retire_an_unrelated_one(tmp_path):
    _queue(tmp_path, [_span(1, "s1")], now=100.0)
    provider = FakeProvider(error=RuntimeError("boom"))
    policy = preset_policy("balanced").with_discovery(max_sessions=1)
    for attempt in range(MAX_SPAN_ATTEMPTS):
        _queue(tmp_path, [_span(90 + attempt, "s2")], now=200.0 + attempt)
        await run_update_discovery(
            tmp_path, provider=provider, policy=policy, now=300.0 + attempt
        )
    with SessionStagingStore.open_default(tmp_path) as store:
        remaining = {row["span_id"] for row in store.pending_discovery_spans(10)}
    assert "span01" not in remaining  # exhausted its retries
    assert remaining  # the untried session behind it is untouched
