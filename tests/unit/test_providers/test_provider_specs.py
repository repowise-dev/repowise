"""The provider specs are the only place a provider is described.

Two contracts. The agent registry and the provider registry name each other
(``AgentIdentity.indexing_provider`` and ``ProviderSpec.agent``), and the two
links must agree. And no module outside the registries branches on, or keeps a
table keyed by, a provider name: that is how the parallel tables this file
replaced drifted.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from repowise.core.agents.identity import all_identities, get_identity, identity_for_provider
from repowise.core.providers.embedding.registry import _BUILTIN_EMBEDDERS
from repowise.core.providers.llm import registry
from repowise.core.providers.llm.registry import get_provider
from repowise.core.providers.llm.specs import PROVIDER_SPECS

_PACKAGES = Path(__file__).parents[3] / "packages"

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


# --- no provider-name branches outside the registries ----------------------

#: Names the AST check looks for: every provider name an embedder does not
#: share. ``ollama``, ``openai`` and the rest are also embedder names, and a
#: literal cannot say which registry it means.
_CHECKED = frozenset(PROVIDER_SPECS) - frozenset(_BUILTIN_EMBEDDERS)

#: Paths (relative to ``packages/``) allowed to spell a provider name.
_ALLOWED = (
    # The registry itself.
    "core/src/repowise/core/providers/llm/specs.py",
    # The other half of the agent <-> provider link.
    "core/src/repowise/core/agents/identity.py",
    # Target ids: ``opencode`` is a ``--target`` as well as a provider.
    "cli/src/repowise/cli/agent_targets/",
    # Commit-trailer patterns naming the agent that wrote a commit.
    "core/src/repowise/core/ingestion/git_indexer/agent_provenance.py",
    # Keyed by model family (``deepseek-v4``, ``kimi-k2``), not by provider.
    "core/src/repowise/core/cost_estimator/pricing.py",
    # Third-party SDK names found in a scanned repo's imports.
    "core/src/repowise/core/ingestion/external_systems/classifier.py",
)


def _is_allowed(path: Path, name: str) -> bool:
    rel = path.relative_to(_PACKAGES).as_posix()
    # A provider's own module tags its errors and reports its own name.
    if rel.startswith("core/src/repowise/core/providers/llm/") and path.stem == name:
        return True
    return rel.startswith(_ALLOWED)


def _name_literals(tree: ast.AST):
    """Provider-name literals used as a comparison operand, a collection
    element or a dict key. A bare argument (``get_provider("x")``) is a use,
    not a branch or a table, and is not flagged."""

    def names(node):
        if isinstance(node, ast.Constant) and node.value in _CHECKED:
            yield node
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for elt in node.elts:
                yield from names(elt)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                yield from names(operand)
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            yield from names(node)
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    yield from names(key)


def _sources():
    return sorted(p for p in _PACKAGES.glob("*/src/**/*.py"))


def test_no_module_branches_on_a_provider_name():
    offenders = sorted(
        {
            f"{path.relative_to(_PACKAGES).as_posix()}:{node.lineno} {node.value!r}"
            for path in _sources()
            for node in _name_literals(ast.parse(path.read_text(encoding="utf-8")))
            if not _is_allowed(path, node.value)
        }
    )
    assert not offenders, (
        "provider names spelled outside the registry; read a ProviderSpec field "
        f"instead: {offenders}"
    )


def test_the_guard_is_looking_at_real_sources():
    """A glob that matched nothing, or a name set that emptied, passes vacuously."""
    assert len(_sources()) > 500
    assert {"claude_cli", "codex_cli", "opencode", "litellm"} <= _CHECKED
