"""Unit tests for the distill engine: safety guarantees around the filters."""

from __future__ import annotations

from typing import ClassVar

import pytest

from repowise.core.distill import distill_output
from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.markers import parse_marker_refs
from repowise.core.distill.registry import filter_registry
from repowise.core.distill.store import OmissionStore


@pytest.fixture()
def crashing_filter():
    """Temporarily register a filter that always raises."""

    class CrashingFilter(OutputFilter):
        name: ClassVar[str] = "crashing"
        priority: ClassVar[int] = 1
        min_lines: ClassVar[int] = 1

        def matches_command(self, command: str) -> bool:
            return command.startswith("crashme")

        def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
            raise RuntimeError("boom")

    instance = CrashingFilter()
    filter_registry._filters.append(instance)
    yield instance
    filter_registry._filters.remove(instance)


def test_distills_and_emits_marker(load_fixture, store: OmissionStore) -> None:
    raw = load_fixture("git_log_full.txt")
    result = distill_output(raw, command="git log -40", store=store)
    assert result.distilled
    assert result.filter_name == "git_log"
    assert result.ref is not None
    assert parse_marker_refs(result.text) == [result.ref]
    assert result.distilled_tokens < result.raw_tokens


def test_marker_ref_round_trips_through_store(load_fixture, store: OmissionStore) -> None:
    raw = load_fixture("git_log_full.txt")
    result = distill_output(raw, command="git log -40", store=store)
    assert store.get(result.ref) == raw


def test_no_matching_filter_returns_raw(store: OmissionStore) -> None:
    raw = "hello\n" * 50
    result = distill_output(raw, command="echo hello", store=store)
    assert not result.distilled
    assert result.text == raw
    assert result.ref is None


def test_filter_exception_falls_back_to_raw(crashing_filter, store: OmissionStore) -> None:
    raw = "some output\n" * 20
    result = distill_output(raw, command="crashme now", store=store)
    assert not result.distilled
    assert result.text == raw


def test_short_output_passes_through(store: OmissionStore) -> None:
    raw = "On branch main\nnothing to commit, working tree clean"
    result = distill_output(raw, command="git status", store=store)
    assert not result.distilled
    assert result.text == raw


def test_no_savings_passes_through(store: OmissionStore) -> None:
    # All-error tsc output: the filter keeps everything, so distillation
    # cannot win and the engine must return raw with no marker.
    raw = "\n".join(
        f"src/file{i}.ts({i},1): error TS2345: Argument of type 'A' is not assignable."
        for i in range(20)
    )
    result = distill_output(raw, command="tsc --noEmit", store=store)
    assert not result.distilled
    assert result.text == raw


def test_store_failure_falls_back_to_raw(load_fixture, store: OmissionStore) -> None:
    # A closed store cannot persist the raw output; without reversibility
    # the engine must not drop a single byte.
    store.close()
    raw = load_fixture("git_log_full.txt")
    result = distill_output(raw, command="git log -40", store=store)
    assert not result.distilled
    assert result.text == raw


def test_savings_recorded_in_ledger(load_fixture, store: OmissionStore) -> None:
    raw = load_fixture("git_log_full.txt")
    distill_output(raw, command="git log -40", source="cli", store=store)
    summary = store.savings_summary()
    assert summary["events"] == 1
    assert summary["per_filter"]["git_log"]["saved_tokens"] > 0


def test_saving_is_capped_at_what_the_host_would_have_delivered(store: OmissionStore) -> None:
    """Output past the host's truncation point was never going to cost anything.

    Claimed savings must be measured against what the agent would have
    *received*, not against the raw byte count, or a huge output books a
    saving for tokens the model would never have seen.
    """
    from repowise.core.distill.engine import HOST_OUTPUT_CAP_CHARS

    # A diffstat far past the cap: 4x the host's limit.
    body = "\n".join(f" packages/mod_{i}/file_{i}.py | {i % 90 + 1} +++---" for i in range(2600))
    raw = body + "\n 2600 files changed, 90000 insertions(+), 40000 deletions(-)\n"
    assert len(raw) > HOST_OUTPUT_CAP_CHARS * 3

    result = distill_output(raw, command="git diff --stat", source="cli", store=store)
    assert result.distilled
    # Billed against the capped counterfactual, not the raw size.
    assert result.raw_tokens == estimate_tokens(raw[:HOST_OUTPUT_CAP_CHARS])
    assert result.raw_tokens < estimate_tokens(raw) // 3
    assert store.savings_summary()["raw_tokens"] == result.raw_tokens
    # …while the marker and the stored artifact still describe the real thing,
    # because `repowise expand` hands back all of it.
    assert result.ref is not None


def test_disabled_filters_respected(load_fixture, store: OmissionStore) -> None:
    raw = load_fixture("git_log_full.txt")
    result = distill_output(raw, command="git log -40", store=store, disabled_filters=("git_log",))
    assert not result.distilled


def test_exit_code_does_not_block_distillation(load_fixture, store: OmissionStore) -> None:
    raw = load_fixture("pytest_fail.txt")
    result = distill_output(raw, command="pytest", exit_code=1, store=store)
    assert result.distilled
    assert result.filter_name == "test_output"
