"""Contract tests for the one place an agent is defined.

The properties here are the ones every downstream namespace assumes without
re-checking: that a slug is syntactically bounded, that the hyphenated target id
is derivable rather than declared, that no two agents answer to the same
announced name, and that an unrecognised announcement becomes ``unknown``
rather than somebody's traffic.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from repowise.core.agents import identity
from repowise.core.agents.identity import (
    UNKNOWN_AGENT,
    AgentIdentity,
    all_identities,
    display_name_for,
    get_identity,
    identity_for_target_id,
    is_agent_slug,
    normalize_client_name,
    register_identity,
    resolve_client_identity,
    unregister_identity,
)

SHIPPED = all_identities()


@pytest.fixture
def seventh_agent() -> Iterator[AgentIdentity]:
    """A registered agent that ships with nothing, removed again afterwards."""
    fake = AgentIdentity(slug="windsurf", display_name="Windsurf")
    register_identity(fake)
    try:
        yield fake
    finally:
        unregister_identity(fake.slug)


# ---------------------------------------------------------------------------
# The slug rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["claude_code", "codex", "unknown", "a", "a" * 32, "x9_0"])
def test_well_formed_slugs_are_accepted(value: str) -> None:
    assert is_agent_slug(value)


@pytest.mark.parametrize(
    "value",
    ["", "a" * 33, "Claude", "claude-code", "claude code", "claude.code", "claude/code", None, 7],
)
def test_malformed_slugs_are_rejected(value: object) -> None:
    assert not is_agent_slug(value)


def test_the_rule_is_syntactic_not_membership() -> None:
    """The property the savings ledger depends on.

    An agent nobody has described — one added tomorrow, or one retired years
    ago whose events are still stored — is still a *valid* id. Only resolution
    of an announced name cares whether we recognise it.
    """
    assert get_identity("agent_from_the_future") is None
    assert is_agent_slug("agent_from_the_future")


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Claude-Code", "claude code", "", "a" * 33])
def test_a_record_refuses_a_malformed_slug(value: str) -> None:
    with pytest.raises(ValueError, match="agent slug"):
        AgentIdentity(slug=value, display_name="Whatever")


def test_no_agent_may_claim_the_unknown_slug() -> None:
    """Otherwise "we could not tell" and "this agent" stop being distinguishable."""
    with pytest.raises(ValueError, match="reserved"):
        AgentIdentity(slug=UNKNOWN_AGENT, display_name="Unknown")


def test_a_record_needs_a_display_name() -> None:
    with pytest.raises(ValueError, match="display name"):
        AgentIdentity(slug="nameless", display_name="")


def test_aliases_must_already_be_normalized() -> None:
    """A denormalized alias would sit in the index where nothing can match it."""
    with pytest.raises(ValueError, match="normalized form"):
        AgentIdentity(slug="x", display_name="X", announced_as=frozenset({"Claude Code"}))


@pytest.mark.parametrize("agent", SHIPPED, ids=lambda a: a.slug)
def test_the_target_id_is_the_hyphenated_slug(agent: AgentIdentity) -> None:
    assert agent.cli_target_id == agent.slug.replace("_", "-")
    assert identity_for_target_id(agent.cli_target_id) is agent


@pytest.mark.parametrize("agent", SHIPPED, ids=lambda a: a.slug)
def test_an_agent_answers_to_its_own_slug_and_display_name(agent: AgentIdentity) -> None:
    """Derived, so a seventh agent resolves without anyone writing an alias down."""
    assert normalize_client_name(agent.slug) in agent.aliases
    assert normalize_client_name(agent.display_name) in agent.aliases


def test_shipped_alias_sets_are_disjoint() -> None:
    seen: dict[str, str] = {}
    for agent in SHIPPED:
        for alias in agent.aliases:
            assert alias not in seen, f"{alias!r} claimed by {seen.get(alias)} and {agent.slug}"
            seen[alias] = agent.slug


def test_registering_a_colliding_alias_is_refused() -> None:
    """Two agents answering to one name is silent misattribution, not a merge."""
    with pytest.raises(ValueError, match="aliases already owned"):
        register_identity(AgentIdentity(slug="impostor", display_name="Claude Code"))
    assert get_identity("impostor") is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("announced", "expected"),
    [
        ("Claude Code", "claude_code"),
        ("claude-code", "claude_code"),
        ("ClaudeCode", "claude_code"),
        ("claude", "claude_code"),
        ("Codex CLI", "codex"),
        ("codex", "codex"),
        ("VS Code", "vscode"),
        ("Cursor", "cursor"),
        ("OpenCode", "opencode"),
        ("Hermes", "hermes"),
    ],
)
def test_announced_names_resolve_through_punctuation_and_case(
    announced: str, expected: str
) -> None:
    assert resolve_client_identity(announced) == expected


@pytest.mark.parametrize(
    "announced", [None, "", "   ", "!!!", "Windsurf", "Untrusted Client!" * 20]
)
def test_absent_or_unrecognised_names_resolve_to_unknown(announced: str | None) -> None:
    assert resolve_client_identity(announced) == UNKNOWN_AGENT


def test_an_unannounced_client_is_never_attributed_to_the_cli_fallback() -> None:
    """The bug this resolver exists to make impossible.

    ``claude-code`` is what ``--target=auto`` falls back to when it detects
    nothing. Reusing that fallback here would file every anonymous MCP client's
    traffic under Claude Code, which is inventing evidence rather than missing it.
    """
    assert resolve_client_identity(None) != "claude_code"
    assert resolve_client_identity(None) == UNKNOWN_AGENT


def test_normalization_bounds_a_hostile_announced_name() -> None:
    assert len(normalize_client_name("Untrusted Client!" * 200)) == 64


def test_registration_invalidates_the_resolver_cache(seventh_agent: AgentIdentity) -> None:
    """The cache is an optimization; it must never outlive the truth."""
    assert resolve_client_identity("Windsurf") == "windsurf"
    unregister_identity("windsurf")
    assert resolve_client_identity("Windsurf") == UNKNOWN_AGENT


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", SHIPPED, ids=lambda a: a.slug)
def test_the_registry_is_where_a_label_comes_from(agent: AgentIdentity) -> None:
    assert display_name_for(agent.slug) == agent.display_name


def test_an_unregistered_slug_is_labelled_verbatim() -> None:
    """A retired agent still in the ledger gets its id back, not an invented name."""
    assert display_name_for("retired_agent") == "retired_agent"
    assert display_name_for(UNKNOWN_AGENT) == UNKNOWN_AGENT


def test_the_shipped_registry_is_the_six_integrated_agents() -> None:
    """Frozen, so a seventh is a deliberate edit rather than a side effect."""
    assert [agent.slug for agent in identity.all_identities()] == [
        "claude_code",
        "codex",
        "vscode",
        "cursor",
        "opencode",
        "hermes",
    ]
