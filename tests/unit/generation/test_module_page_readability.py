"""A module page reads as an explanation: prose, one diagram, few tables.

The measures a reader feels are checked on the page as stored: the share of
lines that are table rows, the word count, and whether headings name what the
module does, not a template slot. A page written to the contract passes
them after every post-processing step; the table-heavy shape the contract
replaces fails them.
"""

from __future__ import annotations

import re
from dataclasses import replace

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.mermaid_safety import invalid_mermaid_blocks
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.mock import MockProvider

_GENERIC_HEADINGS = {
    "overview",
    "architecture",
    "key files",
    "public api",
    "dependencies",
    "summary",
    "introduction",
    "components",
}
_REQUIRED_TAIL = ["Where to start reading", "How it connects"]


def table_line_share(markdown: str) -> float:
    lines = [line for line in markdown.splitlines() if line.strip()]
    return sum(1 for line in lines if line.lstrip().startswith("|")) / max(1, len(lines))


def prose_words(markdown: str) -> int:
    kept, fenced = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and not line.lstrip().startswith(("|", "Sources:", "#")):
            kept.append(line)
    return len(re.findall(r"\b\w[\w'-]*\b", "\n".join(kept)))


def generic_headings(markdown: str) -> list[str]:
    return [
        h for h in re.findall(r"^##\s+(.+?)\s*$", markdown, re.M) if h.lower() in _GENERIC_HEADINGS
    ]


_SECTION = (
    "The {part} part {verb} every order that reaches it. It reads the basket, checks what the "
    "shop still holds and records the outcome before anything is charged. Keeping this step in "
    "one place means a failed check never leaves a half written order behind, and the next "
    "step can trust what it receives. The work is small on purpose: each function does one "
    "thing, and the order moves through them in a fixed sequence that is easy to follow when "
    "something goes wrong. Retries happen here and not further along, because only this "
    "step knows which half of the work already succeeded.\n\n"
    "A reader who wants the rules should open `{file}` first. It keeps the decisions near the "
    "data they depend on, so a change to one rule rarely touches another part of the flow. "
    "Tests for the step sit beside it and describe each rule in plain words."
)

_RESPONSE = "\n\n".join(
    [
        "# Order Handling",
        "Order handling turns a customer's basket into a paid, reserved order. It sits between "
        "the web views that take requests and the store that keeps the results, so the rest of "
        "the shop never deals with half finished orders.",
        "```mermaid\nflowchart LR\n    Orders[\"Orders<br/><small>orders/</small>\"] -->|asks to charge| "
        "Billing[\"Billing<br/><small>billing/</small>\"]\n    Orders -->|reserves stock in| "
        "Stock[\"Stock<br/><small>stock/</small>\"]\n    Web[\"Web views\"]:::ext -->|places orders "
        "through| Orders\n    classDef ext stroke-dasharray:4 3;\n```",
        "## From a basket to a placed order",
        _SECTION.format(part="orders", verb="validates", file="orders/place.py"),
        "Sources: `orders/place.py`",
        "## Taking the payment",
        _SECTION.format(part="billing", verb="charges", file="billing/charge.py"),
        "Sources: `billing/charge.py`",
        "## Holding the stock",
        _SECTION.format(part="stock", verb="reserves stock for", file="stock/reserve.py"),
        "Sources: `stock/reserve.py`",
        "## Where to start reading",
        "- `orders/place.py` - where an order starts.\n"
        "- `billing/charge.py` - how payment is taken.\n"
        "- `stock/reserve.py` - how stock is held.",
        "## How it connects",
        "Web views call into order handling, and order handling writes through the store.",
        "## Questions this page answers",
        "- How is an order placed?\n- Where is payment taken?",
    ]
)

_FILES = ["src/shop/orders/place.py", "src/shop/billing/charge.py", "src/shop/stock/reserve.py"]


class _Provider(MockProvider):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        return replace(response, content=_RESPONSE)


def _context(path: str) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=[],
        imports=[],
        exports=[],
        pagerank_score=0.1,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=10,
    )


async def _page(config, provider=None):
    gen = PageGenerator(provider or _Provider(), ContextAssembler(config), config)
    graph = nx.DiGraph()
    graph.add_nodes_from(_FILES)
    return await gen.generate_module_page(
        "Order Handling",
        "python",
        [_context(f) for f in _FILES],
        graph,
        target_path="src/shop",
        structural_key="k",
    )


async def test_a_contract_page_reads_as_prose(sample_config):
    page = await _page(sample_config)

    assert table_line_share(page.content) == 0
    assert 500 <= prose_words(page.content) <= 800
    assert generic_headings(page.content) == []
    assert invalid_mermaid_blocks(page.content) == []
    assert page.content.count("Sources: `") == 3
    headings = re.findall(r"^##\s+(.+?)\s*$", page.content, re.M)
    assert headings[-2:] == _REQUIRED_TAIL


def test_the_measures_reject_the_table_heavy_shape():
    dump = "## Overview\n\n| File | Role |\n| --- | --- |\n" + "| `a.py` | x |\n" * 12
    assert table_line_share(dump) > 0.5
    assert generic_headings(dump) == ["Overview"]


@pytest.mark.parametrize("style", ["comprehensive", "caveman", "reference", "tutorial"])
def test_every_style_keeps_the_page_shape(sample_config, style):
    config = replace(sample_config, wiki_style=style)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    ctx = gen._assembler.assemble_module_page(
        "Order Handling", "python", [_context(f) for f in _FILES], nx.DiGraph()
    )
    prompt = gen._render("module_page.j2", ctx=ctx)

    for rule in ("fenced `mermaid` block", "Sources: ", "## Where to start reading", "500 to 800"):
        assert rule in prompt
    if style != "comprehensive":
        assert style.upper() in prompt
