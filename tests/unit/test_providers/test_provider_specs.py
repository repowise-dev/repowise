"""The provider specs are the only place a provider is described.

The agent registry and the provider registry name each other
(``AgentIdentity.indexing_provider`` and ``ProviderSpec.agent``), and the two
links must agree.
"""

from __future__ import annotations

import types

import pytest

from repowise.core.agents.identity import all_identities, get_identity, identity_for_provider
from repowise.core.providers.llm import registry
from repowise.core.providers.llm.registry import get_provider
from repowise.core.providers.llm.specs import PROVIDER_SPECS

# --- the link between the two registries -----------------------------------


@pytest.mark.parametrize(
    "agent", [a for a in all_identities() if a.indexing_provider], ids=lambda a: a.slug
)
def test_every_indexing_agent_names_a_spec_that_names_it_back(agent):
    spec = PROVIDER_SPECS.get(agent.indexing_provider)
    assert spec is not None, f"{agent.slug} names unknown provider {agent.indexing_provider!r}"
    assert spec.agent == agent.slug


@pytest.mark.parametrize(
    "spec", [s for s in PROVIDER_SPECS.values() if s.agent], ids=lambda s: s.name
)
def test_every_agent_spec_names_an_identity_that_names_it_back(spec):
    agent = get_identity(spec.agent)
    assert agent is not None, f"{spec.name} names unknown agent {spec.agent!r}"
    assert agent.indexing_provider == spec.name
    assert identity_for_provider(spec.name) is agent
    # The facts the CLI renders when the agent's CLI is missing.
    assert agent.executable and agent.install_hint and agent.login_hint


# --- rate limiting ---------------------------------------------------------


@pytest.mark.parametrize(
    "spec", [s for s in PROVIDER_SPECS.values() if s.agent], ids=lambda s: s.name
)
def test_agent_clis_get_no_rate_limiter(spec, monkeypatch):
    """Concurrency is the only throttle for a subprocess-backed provider."""
    assert spec.rate_limit is None
    seen: dict = {}

    class Fake:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    fake_module = types.SimpleNamespace(**{spec.class_name: Fake})
    monkeypatch.setattr(registry.importlib, "import_module", lambda _path: fake_module)
    get_provider(spec.name)
    assert "rate_limiter" not in seen
