"""A bad argument is named, not answered with a reassuring negative (#1496).

Every test calls the real tool entry point. The bugs these cover survived
because a misspelled filter is a *successful* call: it matches nothing, and an
empty result reads as an answer.
"""

from __future__ import annotations

import pytest


def _entry(result: dict, argument: str) -> dict:
    """The ignored_arguments entry for *argument*, or fail saying what is there."""
    entries = result.get("ignored_arguments", [])
    for e in entries:
        if e["argument"] == argument:
            return e
    pytest.fail(f"no ignored_arguments entry for {argument!r}; got {entries!r}")


class TestDeadCodeKind:
    @pytest.mark.asyncio
    async def test_unknown_kind_is_named_and_not_filtered_on(self, setup_mcp):
        # Issue #1496's own call: the plural is not a kind, and filtering on it
        # reported "No dead code found matching your filters" over 3 findings.
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(kind="unused_exports")

        assert result["summary"]["filtered_findings"] == 3
        assert "No dead code found" not in result["impact"]["recommendation"]
        entry = _entry(result, "kind")
        assert entry["values"] == ["unused_exports"]
        assert "unused_export" in entry["valid"]

    @pytest.mark.asyncio
    async def test_known_kind_still_filters(self, setup_mcp):
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(kind="unused_export")

        assert result["summary"]["filtered_findings"] == 2
        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_valid_kinds_are_the_analyzer_vocabulary(self, setup_mcp):
        # Pinned to the enum the analyzer writes, not to a hand-copied twin:
        # a fifth kind added there must not read as a typo here.
        from repowise.core.analysis.dead_code.models import DeadCodeKind
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(kind="nonsense")

        assert _entry(result, "kind")["valid"] == sorted(k.value for k in DeadCodeKind)


class TestDeadCodeMinConfidence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("high", 1), ("medium", 3), ("low", 3)],
    )
    async def test_tier_names_resolve_to_their_own_bands(self, setup_mcp, value, expected):
        # The response is organised by these words and each tier description
        # states its band; passing one back used to be a float_parsing error.
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(min_confidence=value)

        assert result["summary"]["filtered_findings"] == expected
        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_tier_name_agrees_with_the_tier_it_names(self, setup_mcp):
        # min_confidence="high" must return exactly what tiers.high holds,
        # or the input and output vocabularies mean different things again.
        from repowise.server.mcp_server import get_dead_code

        floored = await get_dead_code(min_confidence="high")
        full = await get_dead_code()

        assert floored["summary"]["filtered_findings"] == full["tiers"]["high"]["count"]

    @pytest.mark.asyncio
    async def test_numeric_string_is_still_a_number(self, setup_mcp):
        # Widening the annotation to float | str is what lets "0.8" through.
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(min_confidence="0.8")

        assert result["summary"]["filtered_findings"] == 1
        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_float_is_unchanged(self, setup_mcp):
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(min_confidence=0.8)

        assert result["summary"]["filtered_findings"] == 1
        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_unrecognised_string_falls_back_and_is_named(self, setup_mcp):
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(min_confidence="hgih")

        assert result["summary"]["filtered_findings"] == 3
        assert _entry(result, "min_confidence")["values"] == ["hgih"]


class TestDeadCodeTier:
    @pytest.mark.asyncio
    async def test_unknown_tier_returns_every_tier_and_is_named(self, setup_mcp):
        # The worst of the three: a wrong tier emptied the whole tiers object,
        # so the response carried no structure to read the miss off.
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(tier="High")

        assert sorted(result["tiers"]) == ["high", "low", "medium"]
        assert _entry(result, "tier")["values"] == ["High"]

    @pytest.mark.asyncio
    async def test_known_tier_still_scopes(self, setup_mcp):
        from repowise.server.mcp_server import get_dead_code

        result = await get_dead_code(tier="high")

        assert sorted(result["tiers"]) == ["high"]
        assert "ignored_arguments" not in result


class TestContextInclude:
    @pytest.mark.asyncio
    async def test_unknown_include_key_is_named(self, setup_mcp):
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"], include=["ownersip"])

        entry = _entry(result, "include")
        assert entry["values"] == ["ownersip"]
        assert "ownership" in entry["valid"]

    @pytest.mark.asyncio
    async def test_several_unknown_keys_share_one_entry(self, setup_mcp):
        # One vocabulary listing however many values miss it — the note ships
        # on the agent surface, where a repeated 12-item list is real budget.
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"], include=["ownersip", "caller"])

        assert len(result["ignored_arguments"]) == 1
        assert _entry(result, "include")["values"] == ["ownersip", "caller"]

    @pytest.mark.asyncio
    async def test_a_known_key_alongside_a_typo_still_resolves(self, setup_mcp):
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"], include=["ownership", "ownersip"])

        assert "ownership" in result["targets"]["src/auth/service.py"]
        assert _entry(result, "include")["values"] == ["ownersip"]

    @pytest.mark.asyncio
    async def test_health_is_a_real_block_not_a_typo(self, setup_mcp):
        # Tested in targets.py but missing from both docstrings, so it was the
        # one live key a hand-written allow-list would have rejected.
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"], include=["health"])

        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_always_on_defaults_are_legal_to_pass(self, setup_mcp):
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"], include=["docs", "freshness"])

        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_clean_call_adds_nothing(self, setup_mcp):
        from repowise.server.mcp_server import get_context

        result = await get_context(["src/auth/service.py"])

        assert "ignored_arguments" not in result


class TestSearchKind:
    @pytest.mark.asyncio
    async def test_unknown_kind_is_named(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("authentication service", kind="tests")

        entry = _entry(result, "kind")
        assert entry["values"] == ["tests"]
        assert entry["valid"] == ["config", "doc", "implementation", "test"]

    @pytest.mark.asyncio
    async def test_known_kind_adds_nothing(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("authentication service", kind="implementation")

        assert "ignored_arguments" not in result

    @pytest.mark.asyncio
    async def test_symbol_mode_carries_the_note_too(self, setup_mcp):
        # search_codebase has three return paths and they are easy to fix one
        # at a time; an identifier-shaped query routes past the concept path.
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService", kind="tests")

        assert _entry(result, "kind")["values"] == ["tests"]
