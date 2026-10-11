"""Code excerpts in module page prompts.

The model reads code, not only paths: the Public API first, then the most
central files' public or documented symbols, three per file. Bodies are capped
at 60 lines and are what a tight budget drops first; a file left without one
keeps a line of signatures. Public API names are always listed.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.generation.context import assembler as assembler_mod
from repowise.core.generation.context.evidence import select_symbol_evidence
from repowise.core.generation.context.module_excerpts import module_excerpts
from repowise.core.generation.context.token_budget import estimate_tokens
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.page_generator import PageGenerator
from tests.unit.generation.test_package_public_api import (
    _api,
    _context,
    _index,
    _RecordingProvider,
)

_REPO = {
    "packages/kit/pyproject.toml": '[project]\nname = "kit"\n',
    "packages/kit/kit/__init__.py": "from .client import Client\n",
    "packages/kit/kit/client.py": (
        "class Client:\n"
        '    """Sends requests."""\n\n'
        "    def send(self, body: str) -> int:\n"
        "        return len(body)\n"
    ),
    "packages/kit/kit/util.py": (
        "def slugify(text: str) -> str:\n"
        '    """Lower-case words joined by dashes."""\n'
        '    return "-".join(text.lower().split())\n\n\n'
        "def _hidden():\n    pass\n"
    ),
}


def _sources(repo: Path, parsed: dict) -> dict[str, bytes]:
    return {p: (repo / p).read_bytes() for p in parsed}


def _excerpt_share(ctx) -> int:
    """Tokens the facts spend on Public API rows, declarations and excerpts."""
    return estimate_tokens(
        json.dumps({k: ctx.facts.get(k) for k in ("public_api", "code_excerpts", "declared")})
    )


def _facts(prompt: str) -> dict:
    """The facts object a module prompt carries."""
    start = prompt.index("```json\n") + len("```json\n")
    return json.loads(prompt[start : prompt.index("\n```", start)])


async def test_prompt_snapshot_has_public_api_excerpts_and_declarations(tmp_path, sample_config):
    parsed = _index(tmp_path, _REPO)
    provider = _RecordingProvider()
    gen = PageGenerator(provider, ContextAssembler(sample_config), sample_config)
    contexts = [_context("packages/kit/kit/util.py"), _context("packages/kit/kit/client.py")]
    contexts[0].pagerank_score = 0.5
    await gen.generate_module_page(
        "Kit",
        "python",
        contexts,
        nx.DiGraph(),
        target_path="packages/kit",
        public_api=_api(parsed, "packages/kit/"),
        parsed_files=parsed,
        source_map=_sources(tmp_path, parsed),
    )
    prompt = provider._calls[-1]["user_prompt"]
    assert "State behaviour only when an excerpt shows it" in prompt
    assert "(historical, <date>)" in prompt
    facts = _facts(prompt)
    # Nothing is left over at this size, so every candidate has its body.
    assert "declared" not in facts
    excerpts = facts["code_excerpts"]
    # Public API first, then the higher-ranked file; private names never.
    assert [e["symbol"] for e in excerpts] == [
        "packages/kit/kit/client.py::Client",
        "packages/kit/kit/util.py::slugify",
    ]
    slugify = excerpts[1]
    assert (slugify["file"], slugify["lines"], slugify["truncated"]) == ("util.py", "1-3", False)
    # Fixtures are written in text mode, so a Windows checkout has CRLF sources.
    assert slugify["code"].replace("\r\n", "\n").startswith(
        "def slugify(text: str) -> str:\n"
        '    """Lower-case words joined by dashes."""\n'
        '    return "-".join(text.lower().split())\n'
    )
    assert "_hidden" not in json.dumps(excerpts)


def _many_functions(n: int, body_lines: int) -> str:
    body = "".join(f"    x{i} = {i}\n" for i in range(body_lines))
    return "".join(f"def f{k}():\n{body}\n\n" for k in range(n))


async def test_budget_drops_bodies_first_and_keeps_every_public_name(
    tmp_path, sample_config, monkeypatch
):
    files = {f"pkg/m{i}.py": _many_functions(3, 40) for i in range(20)}
    files["pkg/__init__.py"] = "".join(f"from .m{i} import f0\n" for i in range(20))
    files["pyproject.toml"] = '[project]\nname = "pkg"\n'
    parsed = _index(tmp_path, files)
    api = _api(parsed, "pkg/")
    budget = 1_500
    monkeypatch.setattr(assembler_mod, "_MODULE_EXCERPT_BUDGET", budget)
    ctx = ContextAssembler(sample_config).assemble_module_page(
        "Pkg",
        "python",
        [_context(p) for p in files if p.endswith(".py")],
        nx.DiGraph(),
        public_api=api,
        parsed_files=parsed,
        source_map=_sources(tmp_path, parsed),
    )
    assert [e["name"] for e in ctx.public_api] == [e["name"] for e in api]
    # Section headings and instructions sit outside the budget; allow for them.
    assert _excerpt_share(ctx) <= budget + 200
    assert 0 < len(ctx.code_excerpts) < 20
    # A file whose body was dropped keeps its signatures.
    assert ctx.declared_files and all("def f0()" in line for line in ctx.declared_files)


async def test_a_long_body_is_capped_at_sixty_lines(tmp_path, sample_config):
    parsed = _index(tmp_path, {"big.py": _many_functions(1, 100)})
    ctx = ContextAssembler(sample_config).assemble_module_page(
        "Big",
        "python",
        [_context("big.py")],
        nx.DiGraph(),
        parsed_files=parsed,
        source_map=_sources(tmp_path, parsed),
    )
    [excerpt] = ctx.code_excerpts
    assert (excerpt["symbol"], excerpt["lines"], excerpt["truncated"]) == ("big.py::f0", "1-60", True)
    assert "x59 = 59" not in excerpt["code"] and "x58 = 58" in excerpt["code"]


def test_public_api_names_past_the_hard_cap_are_counted(sample_config):
    api = [
        {
            "name": f"exported_name_{i}",
            "kind": "function",
            "file": "a.ts",
            "signature": "x" * 400,
            "doc": "",
            "alias_of": "",
        }
        for i in range(6_000)
    ]
    ctx = ContextAssembler(sample_config).assemble_module_page(
        "A", "typescript", [_context("a.ts")], nx.DiGraph(), public_api=api
    )
    assert ctx.public_api_omitted == len(api) - len(ctx.public_api) > 0
    assert _excerpt_share(ctx) <= assembler_mod._MODULE_EXCERPT_HARD_CAP


@pytest.mark.parametrize("budget", [60, 200, 10_000])
def test_line_capped_excerpts_stay_whole_in_priority_order(tmp_path, budget):
    parsed = _index(tmp_path, {"m.py": _many_functions(4, 5)})
    refs = [f"m.py::f{k}" for k in range(4)]
    sel = select_symbol_evidence(
        _sources(tmp_path, parsed),
        refs,
        list(parsed.values()),
        token_budget=budget,
        max_lines=60,
    )
    assert sel.estimated_tokens <= budget
    assert not any(item.truncated for item in sel.included)
    kept = [item.symbol for item in sel.included]
    assert kept == refs[: len(kept)]
    if budget == 10_000:
        assert kept == refs


def test_a_big_public_api_body_is_not_displaced_by_a_smaller_ranked_one(tmp_path):
    parsed = _index(
        tmp_path, {"big.py": _many_functions(1, 50), "small.py": "def g():\n    return 1\n"}
    )
    sources = _sources(tmp_path, parsed)
    api = [{"name": "f0", "id": "big.py::f0", "file": "big.py"}]
    # Room for the small body alone, not the big one.
    budget = 260
    selection, declared = module_excerpts(api, parsed, ["small.py"], sources, budget)
    alone, _ = module_excerpts([], parsed, ["small.py"], sources, budget)
    assert [item.symbol for item in alone.included] == ["small.py::g"]
    assert selection.included == ()
    assert any("big.py" in line for line in declared)
