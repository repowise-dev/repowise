"""The provider specs are the only place a provider is described.

Two contracts. The agent registry and the provider registry name each other
(``AgentIdentity.indexing_provider`` and ``ProviderSpec.agent``), and the two
links must agree. And no module outside the registries branches on, or keeps a
table keyed by, a provider name: that is how the parallel tables this file
replaced drifted.
"""

from __future__ import annotations

import ast
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

#: Every provider name but ``mock``, which is also the default embedder and the
#: ordinary test-double word that health and dead-code analysis match on.
_CHECKED = frozenset(PROVIDER_SPECS) - {"mock"}

_EMBEDDERS = frozenset(_BUILTIN_EMBEDDERS)

#: Path (relative to ``packages/``) -> names it may spell, and why.
_ALLOWED: dict[str, frozenset[str]] = {
    # ``opencode`` is a ``--target`` id as well as a provider.
    "cli/src/repowise/cli/agent_targets/registry.py": frozenset({"opencode"}),
    # Commit-trailer patterns naming the agent that wrote a commit.
    "core/src/repowise/core/ingestion/git_indexer/agent_provenance.py": frozenset(
        {"gemini", "opencode"}
    ),
    # Price keys are model families (``deepseek-v4``, ``kimi-k2``, ``gemini``).
    "core/src/repowise/core/cost_estimator/pricing.py": frozenset({"deepseek", "gemini", "kimi"}),
    # Third-party SDK names found in a scanned repo's imports.
    "core/src/repowise/core/ingestion/external_systems/classifier.py": frozenset(
        {"anthropic", "openai"}
    ),
    # Behaviour only the OpenAI adapter has: the custom-gateway row and base-url
    # prompt, and its reasoning budget. Ollama's readiness is a TCP probe.
    "cli/src/repowise/cli/helpers.py": frozenset({"openai"}),
    "cli/src/repowise/cli/ui/provider_selection.py": frozenset({"openai", "ollama"}),
    "server/src/repowise/server/mcp_server/tool_answer/synthesis.py": frozenset({"openai"}),
    # Embedder surfaces: these literals name embedders, which share spellings.
    "core/src/repowise/core/providers/embedding/registry.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/init_cmd/command.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/reindex_cmd.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/serve_cmd.py": _EMBEDDERS,
    "cli/src/repowise/cli/commands/update_cmd/deterministic.py": _EMBEDDERS,
    "cli/src/repowise/cli/providers/embedders.py": _EMBEDDERS,
    "cli/src/repowise/cli/providers/keys.py": _EMBEDDERS,
    "cli/src/repowise/cli/ui/mode_selection.py": _EMBEDDERS,
    "server/src/repowise/server/app.py": _EMBEDDERS,
    "server/src/repowise/server/mcp_server/_server.py": _EMBEDDERS,
}


def _is_allowed(path: Path, name: str) -> bool:
    rel = path.relative_to(_PACKAGES).as_posix()
    # A provider's own module tags its errors and reports its own name.
    if rel.startswith("core/src/repowise/core/providers/llm/") and path.stem == name:
        return True
    return name in _ALLOWED.get(rel, frozenset())


def _name_literals(tree: ast.AST):
    """Provider-name literals used as a comparison operand, a ``case`` value, a
    collection element, a dict key or a ``.get()`` key. A bare argument
    (``get_provider("x")``) is a use, not a branch or a table, and is not
    flagged."""

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
        elif isinstance(node, ast.MatchValue):
            yield from names(node.value)
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            yield from names(node)
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    yield from names(key)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            yield from names(node.args[0])


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


def test_the_allowlist_has_no_stale_entries():
    """An entry that no longer matches anything would quietly widen later."""
    for rel, names in _ALLOWED.items():
        path = _PACKAGES / rel
        assert path.is_file(), f"{rel} is gone"
        found = {node.value for node in _name_literals(ast.parse(path.read_text(encoding="utf-8")))}
        assert found & names, f"{rel} no longer spells any of {sorted(names)}"


@pytest.mark.parametrize(
    "snippet",
    [
        'x == "codex_cli"',
        'x in ("claude_cli", "y")',
        '{"opencode": 1}',
        'd.get("litellm")',
        'match x:\n    case "kimi":\n        pass',
    ],
)
def test_the_guard_sees_each_branch_shape(snippet):
    assert list(_name_literals(ast.parse(snippet)))


def test_the_guard_is_looking_at_real_sources():
    """A glob that matched nothing, or a name set that emptied, passes vacuously."""
    assert len(_sources()) > 500
    assert {"claude_cli", "codex_cli", "opencode", "litellm", "openai"} <= _CHECKED


# --- no provider or embedder lists in the web UI ----------------------------

#: A quoted provider/embedder name, or one used as an object key. The UI reads
#: these from ``/api/providers``; ``mock`` is checked here, unlike above.
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
