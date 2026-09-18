"""The descriptor/identity seam, and the promise that a seventh agent is cheap.

Parameterized across ``_TARGET_MODULES`` rather than written per agent, for the
same reason ``test_agent_targets.py`` is: the property under test is that the
seam holds for *every* registered target, and a per-agent test file would
quietly not have that property for the next one added.

``INTEGRATIONS.md`` promises adding an agent costs one descriptor file and one
registry line. The tests at the bottom are what stops savings attribution from
silently not being included in that promise.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.cli.agent_targets.registry import _TARGET_MODULES, get_target
from repowise.core.agents.identity import (
    UNKNOWN_AGENT,
    AgentIdentity,
    display_name_for,
    identity_for_target_id,
    is_agent_slug,
    register_identity,
    resolve_client_identity,
    unregister_identity,
)
from repowise.core.distill.store import OmissionStore
from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent
from repowise.core.savings.correlation import scoped_idempotency_key
from repowise.core.savings.normalization import normalize_mcp_identity
from repowise.core.savings.repository import SavingsRepository

ALL_IDS = list(_TARGET_MODULES)

#: The agent this file pretends repowise integrated with. Nothing under
#: ``core/savings/`` mentions it, which is the whole point.
SEVENTH = AgentIdentity(
    slug="windsurf",
    display_name="Windsurf",
    hook_adapter="windsurf",
    session_adapter="windsurf",
)


# ---------------------------------------------------------------------------
# Every registered target resolves an identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_every_target_is_backed_by_an_identity(target_id: str) -> None:
    assert identity_for_target_id(target_id) is not None


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_a_descriptor_does_not_restate_what_its_identity_says(target_id: str) -> None:
    """The duplication this seam exists to remove.

    ``id``, ``display_name`` and the two adapter names used to be typed into the
    descriptor beside the identical values elsewhere. They are now one value
    each, read from one record, so they cannot drift apart by hand.
    """
    target = get_target(target_id)
    agent = identity_for_target_id(target_id)
    assert target is not None and agent is not None
    assert target.id == agent.cli_target_id
    assert target.display_name == agent.display_name
    assert target.hook_adapter == agent.hook_adapter
    assert target.session_adapter == agent.session_adapter


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_the_published_target_id_is_the_registry_key(target_id: str) -> None:
    """``--target=`` ids are documented and typed by users; they may not move."""
    agent = identity_for_target_id(target_id)
    assert agent is not None
    assert agent.cli_target_id == target_id
    assert is_agent_slug(agent.slug)


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_every_alias_resolves_back_to_its_own_agent(target_id: str) -> None:
    agent = identity_for_target_id(target_id)
    assert agent is not None
    for alias in agent.aliases:
        assert resolve_client_identity(alias) == agent.slug


def test_no_two_targets_answer_to_the_same_announced_name() -> None:
    claimed: dict[str, str] = {}
    for target_id in ALL_IDS:
        agent = identity_for_target_id(target_id)
        assert agent is not None
        for alias in agent.aliases:
            assert alias not in claimed, (
                f"{alias!r} claimed by {claimed.get(alias)} and {agent.slug}"
            )
            claimed[alias] = agent.slug


# ---------------------------------------------------------------------------
# A seventh agent, end to end
# ---------------------------------------------------------------------------


class _WindsurfTarget:
    """A descriptor for an agent repowise does not actually integrate with.

    Only the identity-derived attributes are real; nothing here touches the
    filesystem, because the chain under test is attribution, not installation.
    """

    id = SEVENTH.cli_target_id
    display_name = SEVENTH.display_name
    docs_url = None
    hook_adapter = SEVENTH.hook_adapter
    session_adapter = SEVENTH.session_adapter
    methods = ()
    project_file_id = "windsurf_rules"


TARGET = _WindsurfTarget()


@pytest.fixture
def seventh_agent(monkeypatch: pytest.MonkeyPatch) -> Iterator[AgentIdentity]:
    """Add the agent exactly as shipping one would: an identity and a registry line."""
    register_identity(SEVENTH)
    monkeypatch.setitem(_TARGET_MODULES, SEVENTH.cli_target_id, f"{__name__}:TARGET")
    try:
        yield SEVENTH
    finally:
        unregister_identity(SEVENTH.slug)


def test_a_seventh_agent_costs_a_descriptor_and_a_registry_line(
    seventh_agent: AgentIdentity,
) -> None:
    target = get_target("windsurf")
    assert target is not None
    assert target.id == "windsurf"
    assert target.display_name == "Windsurf"
    assert identity_for_target_id("windsurf") is seventh_agent


def test_a_seventh_agent_is_attributed_rather_than_bucketed_as_unknown(
    seventh_agent: AgentIdentity,
) -> None:
    """The regression this phase exists to prevent.

    Before the vocabulary was unified, a newly added agent announced a name the
    savings domain had never heard of, so every one of its events landed in
    ``unknown`` permanently — while an adapter that passed the new id directly
    raised instead, on a write path behind live MCP calls.
    """
    assert resolve_client_identity("Windsurf") == seventh_agent.slug
    assert resolve_client_identity("Windsurf") != UNKNOWN_AGENT


def test_a_seventh_agents_events_record_and_report_with_no_savings_edit(
    tmp_path: Path, seventh_agent: AgentIdentity
) -> None:
    """The exit criterion, proved rather than inspected.

    Nothing under ``core/savings/`` names this agent, and nothing needed to: the
    event validates, stores, survives the database's own CHECK constraints, and
    comes back out of the report attributed to it.
    """
    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        event = SavingsEvent.from_mapping(
            {
                "idempotency_key": scoped_idempotency_key("repo", "mcp", 1),
                "occurred_at": "2026-09-18T00:00:01Z",
                "repository_id": "repo",
                "surface": "mcp",
                "integration": seventh_agent.slug,
                "agent": seventh_agent.slug,
                "operation": "get_risk",
                "evidence_kind": "measured",
                "estimator": "seventh_agent_test",
                "token_unit": "estimated_tokens",
                "result_state": "success",
                "is_usable": True,
                "baseline_input_tokens": 100,
                "pre_budget_input_tokens": 100,
                "delivered_input_tokens": 40,
            }
        )
        assert event.agent == event.integration == seventh_agent.slug
        assert repository.record_event(event) is True
        assert (
            repository.record_opportunity(
                OpportunityObservation.from_mapping(
                    {
                        "observation_id": "obs-1",
                        "occurred_at": "2026-09-18T00:00:02Z",
                        "repository_id": "repo",
                        "integration": seventh_agent.slug,
                        "kind": "bypassed_distillation",
                        "estimated_potential_input_tokens": 500,
                    }
                )
            )
            is True
        )

        report = repository.report("repo", as_of=datetime(2026, 9, 19, tzinfo=UTC))
        assert report.saved_input_tokens == 60
        assert report.opportunity_count == 1
        assert report.opportunity_tokens_excluded == 500
        stored = store._conn.execute("SELECT agent, integration FROM savings_events").fetchone()
        assert tuple(stored) == (seventh_agent.slug, seventh_agent.slug)

    assert display_name_for(seventh_agent.slug) == "Windsurf"


def test_a_retired_agents_events_still_read_back(tmp_path: Path) -> None:
    """The other half of a syntactic rule: nothing is enumerated on the way out.

    ``deprecated_agent`` has no identity record at all. A membership check would
    reject the write, or coerce the read to ``unknown`` and lose the attribution
    that was recorded when the agent did exist.
    """
    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        event = SavingsEvent.from_mapping(
            {
                "idempotency_key": scoped_idempotency_key("repo", "mcp", 2),
                "occurred_at": "2026-09-18T00:00:03Z",
                "repository_id": "repo",
                "surface": "mcp",
                "integration": "deprecated_agent",
                "agent": "deprecated_agent",
                "operation": "get_risk",
                "evidence_kind": "measured",
                "estimator": "seventh_agent_test",
                "token_unit": "estimated_tokens",
                "result_state": "success",
                "is_usable": True,
                "baseline_input_tokens": 10,
                "pre_budget_input_tokens": 10,
                "delivered_input_tokens": 4,
            }
        )
        assert repository.record_event(event) is True
        stored = store._conn.execute("SELECT agent FROM savings_events").fetchone()[0]
        assert stored == "deprecated_agent"
    assert display_name_for("deprecated_agent") == "deprecated_agent"


@pytest.mark.parametrize("field", ["integration", "agent"])
def test_writer_and_reader_reject_a_malformed_id_the_same_way(field: str) -> None:
    """These two used to disagree: one raised, the other coerced to ``unknown``."""
    payload = {
        "idempotency_key": scoped_idempotency_key("repo", "mcp", 3),
        "occurred_at": "2026-09-18T00:00:04Z",
        "repository_id": "repo",
        "surface": "mcp",
        "integration": "codex",
        "agent": "codex",
        "operation": "get_risk",
        "evidence_kind": "measured",
        "estimator": "seventh_agent_test",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": 10,
        "pre_budget_input_tokens": 10,
        "delivered_input_tokens": 4,
    }
    with pytest.raises(ValueError, match=f"{field} must be a bounded"):
        SavingsEvent.from_mapping({**payload, field: "Windsurf!"})
    with pytest.raises(ValueError, match="integration must be a bounded"):
        OpportunityObservation.from_mapping(
            {
                "observation_id": "obs-2",
                "occurred_at": "2026-09-18T00:00:05Z",
                "repository_id": "repo",
                "integration": "Windsurf!",
                "kind": "bypassed_distillation",
                "estimated_potential_input_tokens": 1,
            }
        )


@pytest.mark.parametrize("target_id", ALL_IDS)
def test_the_adapter_names_an_identity_declares_actually_exist(target_id: str) -> None:
    """The last hand-agreement this change did not already remove.

    ``hook_adapter`` and ``session_adapter`` are strings pointing into two
    registries the identity record does not own, and ``derive_tier`` reads both
    to decide the Full tier the README badges repeat. A typo in either would
    otherwise claim a depth that does not exist, silently, until something tried
    to load the adapter.
    """
    from repowise.cli.agent_adapters import _REGISTRY
    from repowise.core.sessions.adapters import registered_adapters

    agent = identity_for_target_id(target_id)
    assert agent is not None
    if agent.hook_adapter is not None:
        assert agent.hook_adapter in _REGISTRY
    if agent.session_adapter is not None:
        assert agent.session_adapter in registered_adapters()


def test_an_announced_name_reaches_the_stored_attribution(
    tmp_path: Path, seventh_agent: AgentIdentity
) -> None:
    """The chain the commit claims, driven end to end rather than in halves.

    A host announces a display name over MCP; that name is resolved once; the
    resolved slug is what lands in the ``agent`` column and comes back out of
    the report. Nothing between those two ends enumerates agents.
    """
    resolved, metadata = normalize_mcp_identity("Windsurf")
    assert resolved == seventh_agent.slug
    assert metadata["client_info_normalized"] == "windsurf"

    with OmissionStore(tmp_path / "omissions.db") as store:
        repository = SavingsRepository(store._conn)
        repository.record_event(
            SavingsEvent.from_mapping(
                {
                    "idempotency_key": scoped_idempotency_key("repo", "mcp", 9),
                    "occurred_at": "2026-09-18T00:00:09Z",
                    "repository_id": "repo",
                    "surface": "mcp",
                    "integration": resolved,
                    "agent": resolved,
                    "operation": "get_risk",
                    "evidence_kind": "measured",
                    "estimator": "seventh_agent_test",
                    "token_unit": "estimated_tokens",
                    "result_state": "success",
                    "is_usable": True,
                    "metadata": metadata,
                    "baseline_input_tokens": 100,
                    "pre_budget_input_tokens": 100,
                    "delivered_input_tokens": 40,
                }
            )
        )
        stored = store._conn.execute("SELECT agent FROM savings_events").fetchone()[0]
    assert stored == "windsurf"
    assert display_name_for(stored) == "Windsurf"


def test_an_unannounced_client_is_stored_as_unknown_not_as_the_fallback() -> None:
    """Absent evidence stays absent all the way into the row."""
    resolved, _ = normalize_mcp_identity(None)
    assert resolved == UNKNOWN_AGENT
    assert resolved != identity_for_target_id("claude-code").slug  # type: ignore[union-attr]
