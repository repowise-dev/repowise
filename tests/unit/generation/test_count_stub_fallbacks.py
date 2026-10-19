"""The shared split every GenerationJob writer needs.

Four places record how many pages a run wrote: ``init``, ``update``, the
upgrade flow, and the two server job paths. A stub the generator substituted
for a failed provider call (issue #1089) sits in ``generated_pages`` like any
other page, so each of them counting the list would report an outage as a
clean run. They share this helper so the four cannot drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.generation.models import (
    STUB_FALLBACK_ERROR,
    GeneratedPage,
    count_stub_fallbacks,
    is_stub_fallback,
)


def _page(target: str, *, stub_error: str | None = None) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    page = GeneratedPage(
        page_id=f"module_page:{target}",
        page_type="module_page",
        title=target,
        content="body",
        source_hash="x" * 64,
        model_name="mock",
        provider_name="template" if stub_error else "mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=4,
        target_path=target,
        created_at=now,
        updated_at=now,
    )
    if stub_error:
        page.metadata[STUB_FALLBACK_ERROR] = stub_error
    return page


def test_counts_only_the_pages_a_provider_failure_produced():
    pages = [_page("a"), _page("b", stub_error="529"), _page("c", stub_error="timeout")]

    assert count_stub_fallbacks(pages) == 2


def test_a_clean_run_counts_zero():
    assert count_stub_fallbacks([_page("a"), _page("b")]) == 0


def test_empty_is_zero():
    assert count_stub_fallbacks([]) == 0


def test_a_deterministic_stub_is_not_a_failure():
    """A template page nothing failed on. Same ``provider_name``, no marker."""
    deterministic = _page("a")
    deterministic.provider_name = "template"

    assert is_stub_fallback(deterministic) is False
    assert count_stub_fallbacks([deterministic]) == 0


def test_tolerates_a_page_without_metadata():
    """The server paths hand this whatever the pipeline returned."""

    class _Bare:
        pass

    assert count_stub_fallbacks([_Bare()]) == 0


def test_dominant_hint_is_the_one_most_failed_pages_share():
    from repowise.core.generation.models import STUB_FALLBACK_HINT, dominant_stub_fallback_hint

    auth = {"kind": "auth", "message": "API key rejected by openai. Update it in Settings."}
    pages = [_page("a", stub_error="401"), _page("b", stub_error="401"), _page("c", stub_error="x")]
    pages[0].metadata[STUB_FALLBACK_HINT] = auth
    pages[1].metadata[STUB_FALLBACK_HINT] = auth

    assert dominant_stub_fallback_hint(pages) == auth
    assert dominant_stub_fallback_hint([_page("d"), pages[2]]) is None


def test_a_stub_for_a_rejected_key_carries_the_hint():
    from repowise.core.generation.models import STUB_FALLBACK_HINT
    from repowise.core.generation.page_generator.pertype import _stub_fallback
    from repowise.core.providers.llm.base import ProviderError

    page = _stub_fallback(_page("a"), "module_page", ProviderError("openai", "bad key", 401))
    plain = _stub_fallback(_page("b"), "module_page", ValueError("artifact check"))

    assert page.metadata[STUB_FALLBACK_HINT]["kind"] == "auth"
    assert STUB_FALLBACK_HINT not in plain.metadata
