"""Phase-2 LLM-docs decision harvest: parsing, the anti-hallucination gate,
and the harvest-directive toggle.

These cover the critical guardrail — because the generator now *generates*
decision candidates, a fluent-but-invented rationale/quote that is not grounded
in the file's source must be dropped, and a candidate with no surviving
grounded field must be rejected outright.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator.core import PageGenerator
from repowise.core.generation.page_generator.decision_harvest import (
    HARVEST_DIRECTIVE,
    harvest_decisions,
    parse_and_strip_decisions,
)

# A small verbatim file source the harvested quotes are gated against.
_SOURCE = (
    "import redis\n\n"
    "# We use Redis as the cache backend because it gives us sub-millisecond\n"
    "# reads and a shared store across workers.\n"
    "CACHE = redis.Redis(host='localhost')\n"
)


def _fenced(json_body: str) -> str:
    return (
        f"# Overview\n\nThis module wires the cache.\n\n```repowise-decisions\n{json_body}\n```\n"
    )


# ---------------------------------------------------------------------------
# parse_and_strip_decisions
# ---------------------------------------------------------------------------


def test_parse_strips_fence_and_returns_payload():
    content = _fenced('{"decisions": [{"title": "Use Redis", "source_quote": "use Redis"}]}')
    clean, decisions = parse_and_strip_decisions(content)

    assert "repowise-decisions" not in clean
    assert "```" not in clean
    assert clean.endswith("wires the cache.")
    assert len(decisions) == 1
    assert decisions[0]["title"] == "Use Redis"


def test_parse_no_fence_is_passthrough():
    content = "# Overview\n\nNothing to harvest here.\n"
    clean, decisions = parse_and_strip_decisions(content)
    assert clean == content
    assert decisions == []


def test_parse_malformed_json_does_not_corrupt_page():
    content = _fenced("{not valid json")
    clean, decisions = parse_and_strip_decisions(content)
    # Fence still stripped; payload simply empty rather than raising.
    assert "repowise-decisions" not in clean
    assert decisions == []


def test_parse_drops_titleless_items():
    content = _fenced('{"decisions": [{"source_quote": "use Redis"}, {"title": "Use Redis"}]}')
    _clean, decisions = parse_and_strip_decisions(content)
    assert [d["title"] for d in decisions] == ["Use Redis"]


# ---------------------------------------------------------------------------
# harvest_decisions — the gate
# ---------------------------------------------------------------------------


def test_grounded_decision_is_kept_as_llm_inferred():
    content = _fenced(
        '{"decisions": [{"title": "Use Redis for caching", '
        '"decision": "We use Redis as the cache backend", '
        '"rationale": "sub-millisecond reads and a shared store across workers", '
        '"source_quote": "We use Redis as the cache backend"}]}'
    )
    clean, decisions = harvest_decisions(content, source_text=_SOURCE, evidence_file="cache.py")

    assert "repowise-decisions" not in clean
    assert len(decisions) == 1
    d = decisions[0]
    assert d["source"] == "llm_inferred"
    assert d["status"] == "proposed"
    assert d["evidence_file"] == "cache.py"
    assert d["affected_files"] == ["cache.py"]
    # Grounded in the verbatim source → exact verdict, fields preserved.
    assert d["verification"] == "exact"
    assert d["decision"] == "We use Redis as the cache backend"
    # Transient source span must never leak into persistence.
    assert d["source_text"] == ""


def test_hallucinated_decision_is_rejected():
    # None of the produced fields appear in the source — a pure fabrication.
    content = _fenced(
        '{"decisions": [{"title": "Adopt Kafka event bus", '
        '"decision": "All services publish domain events to Kafka", '
        '"rationale": "decouples producers from consumers at scale", '
        '"source_quote": "All services publish domain events to Kafka"}]}'
    )
    clean, decisions = harvest_decisions(content, source_text=_SOURCE, evidence_file="cache.py")
    assert decisions == [], "an ungrounded harvested decision must be dropped by the gate"
    assert "repowise-decisions" not in clean


def test_partial_hallucination_drops_only_ungrounded_fields():
    # source_quote is grounded; rationale is invented. The decision survives
    # with the invented field cleared.
    content = _fenced(
        '{"decisions": [{"title": "Use Redis", '
        '"decision": "We use Redis as the cache backend", '
        '"rationale": "mandated by the platform compliance committee", '
        '"source_quote": "We use Redis as the cache backend"}]}'
    )
    _clean, decisions = harvest_decisions(content, source_text=_SOURCE, evidence_file="cache.py")
    assert len(decisions) == 1
    d = decisions[0]
    assert d["decision"] == "We use Redis as the cache backend"
    assert d["rationale"] == "", "the ungrounded rationale must be dropped"


def test_title_promoted_to_decision_when_gate_clears_paraphrase():
    # The decision/rationale are real but paraphrased, so the substring gate
    # clears them; the verbatim source_quote keeps the record alive. Without the
    # title fallback this leaves a body-less, title-only record. The title (the
    # model's canonical one-line summary) must be promoted into ``decision``.
    content = _fenced(
        '{"decisions": [{"title": "Use Redis for caching", '
        '"decision": "Persistence layer selected by the platform team", '
        '"rationale": "latency targets demanded an alternative approach", '
        '"source_quote": "We use Redis as the cache backend"}]}'
    )
    _clean, decisions = harvest_decisions(content, source_text=_SOURCE, evidence_file="cache.py")
    assert len(decisions) == 1
    d = decisions[0]
    # Paraphrased prose was cleared by the gate...
    assert d["rationale"] == ""
    # ...but the record is not body-less: the title filled the empty decision.
    assert d["decision"] == "Use Redis for caching"
    # The grounding quote that kept the record alive is still recorded.
    assert d["source_quote"] == "We use Redis as the cache backend"


def test_no_fence_yields_no_decisions():
    clean, decisions = harvest_decisions(
        "# Overview\n\nPlain page.\n", source_text=_SOURCE, evidence_file="cache.py"
    )
    assert decisions == []
    # No fence → content returned verbatim (only the fence path trims).
    assert clean == "# Overview\n\nPlain page.\n"


# ---------------------------------------------------------------------------
# Harvest directive toggle (the --no-harvest-decisions escape hatch)
# ---------------------------------------------------------------------------


def _make_generator(harvest: bool) -> PageGenerator:
    provider = MagicMock()
    provider.model_name = "mock-model"
    provider.provider_name = "mock"
    config = GenerationConfig(harvest_decisions=harvest)
    return PageGenerator(provider, MagicMock(), config)


def test_directive_reaches_no_system_prompt():
    """Harvesting only ever ran on ``file_page``, and a file page no longer
    goes near a model, so nothing carries the directive.

    The parser below still has tests because the payload format is worth
    keeping intact, but there is no producer feeding it. Whether harvesting
    gets a new host or is retired is a decision of its own rather than a side
    effect of the file layer becoming structural.
    """
    gen = _make_generator(harvest=True)
    for page_type in ("module_page", "repo_overview", "architecture_diagram", "onboarding"):
        assert HARVEST_DIRECTIVE not in gen._build_system_prompt(page_type)
