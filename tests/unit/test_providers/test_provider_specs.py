"""The provider specs are the only place a provider is described.

Two contracts. The agent registry and the provider registry name each other
(``AgentIdentity.indexing_provider`` and ``ProviderSpec.agent``), and the two
links must agree. And no module outside the registries branches on, or keeps a
table keyed by, a provider name: that is how the parallel tables this file
replaced drifted. The Python half of that second contract reads the whole
source tree, so it lives in ``tests/unit/test_source_tree_guards.py``; the web
UI half is here.
"""

from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from repowise.core.agents.identity import all_identities, get_identity, identity_for_provider
from repowise.core.providers.embedding.registry import _BUILTIN_EMBEDDERS
from repowise.core.providers.llm import registry
from repowise.core.providers.llm.registry import get_provider
from repowise.core.providers.llm.specs import PROVIDER_SPECS

_PACKAGES = Path(__file__).parents[3] / "packages"
_EMBEDDERS = frozenset(_BUILTIN_EMBEDDERS)

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


# --- no provider or embedder lists in the web UI ----------------------------

#: A quoted provider/embedder name, or one used as an object key. The UI reads
#: these from ``/api/providers``; ``mock`` is checked here, unlike in the Python guard.
_UI_NAME = re.compile(
    r"""["'](?P<q>{names})["']|^\s*(?P<k>{names})\s*:""".format(
        names="|".join(sorted(frozenset(PROVIDER_SPECS) | _EMBEDDERS, key=len, reverse=True))
    ),
    re.MULTILINE,
)


def _ui_sources():
    return sorted(
        p
        for pkg in ("ui", "web", "api-client")
        for p in (_PACKAGES / pkg / "src").rglob("*.ts*")
        if p.suffix in {".ts", ".tsx"}
        and ".test." not in p.name
        and "__tests__" not in p.parts
        and "generated" not in p.parts
    )


def test_no_ui_source_spells_a_provider_name():
    offenders = [
        f"{path.relative_to(_PACKAGES).as_posix()}:"
        f"{text.count(chr(10), 0, m.start()) + 1} {m.group('q') or m.group('k')!r}"
        for path in _ui_sources()
        for text in [path.read_text(encoding="utf-8")]
        for m in _UI_NAME.finditer(text)
    ]
    assert not offenders, f"provider names spelled in UI source; read /api/providers: {offenders}"


@pytest.mark.parametrize(
    "snippet", ['x === "mock"', "['gemini', 'openai']", "  ollama: [],", 'useState("litellm")']
)
def test_the_ui_guard_sees_each_shape(snippet):
    assert _UI_NAME.search(snippet)


def test_the_ui_guard_is_looking_at_real_sources():
    assert len(_ui_sources()) > 200
