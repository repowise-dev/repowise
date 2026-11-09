"""Model-written pages held to their own template: shape rules, link repair,
the neighbours section, and which traced flow a page draws."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.execution_flows import rank_by_importance
from repowise.core.generation.context.module_facts import connections_markdown
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.overview_tables import embed_section
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.validation import (
    PageShapeError,
    validate_page_shape,
)
from repowise.core.generation.prose import unwrap_markdown_links
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

_SECTION = "The parser reads one file and records each symbol it declares. " * 6


def _page(*, opening: str = "The parser turns bytes into symbols.", extra: str = "") -> str:
    body = "\n\n".join(
        [
            "# Parsing",
            opening,
            "## From bytes to symbols",
            _SECTION,
            "Sources: `parser.py`",
            extra,
            "## Where to start reading",
            "- `parser.py` - the entry.",
        ]
    )
    return body + "\n\n## Questions this page answers\n\n- How is a file parsed?\n"


# ---------------------------------------------------------------------------
# Shape rules
# ---------------------------------------------------------------------------


def test_a_page_that_keeps_its_template_passes():
    validate_page_shape("module_page", _page(opening="`ASTParser` reads one file at a time."))


def test_house_jargon_is_rejected():
    opening = "This subsystem sits at the boundary of ingestion; see the child page."
    with pytest.raises(PageShapeError, match="abstract vocabulary"):
        validate_page_shape("module_page", _page(opening=opening))


def test_one_stray_word_is_tolerated():
    """"word boundary" is a fine phrase on a regex page."""
    validate_page_shape("module_page", _page(opening="Splits on each word boundary."))


def test_jargon_inside_code_is_not_prose():
    code = "```python\n# the subsystem boundary\nsubsystem = boundary\n```"
    validate_page_shape("module_page", _page(extra=code))


def test_a_fourth_table_is_rejected():
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    validate_page_shape("module_page", _page(extra="\n\ntext\n\n".join([table] * 3)))
    with pytest.raises(PageShapeError, match="4 tables"):
        validate_page_shape("module_page", _page(extra="\n\ntext\n\n".join([table] * 4)))


def test_a_parts_table_fits_but_a_reference_table_does_not():
    rows = "\n".join(f"| `f{i}.py` | x |" for i in range(20))
    validate_page_shape("module_page", _page(extra=f"| Part | Owns |\n|---|---|\n{rows}"))
    rows = "\n".join(f"| `f{i}.py` | x |" for i in range(40))
    with pytest.raises(PageShapeError, match="40 rows"):
        validate_page_shape("module_page", _page(extra=f"| File | Role |\n|---|---|\n{rows}"))


def test_an_overlong_page_is_rejected_and_the_questions_do_not_count():
    with pytest.raises(PageShapeError, match="words of prose"):
        validate_page_shape("module_page", _page(extra=_SECTION * 30))
    questions = "\n".join(f"- Question {i} about parsing files?" for i in range(150))
    validate_page_shape("module_page", _page() + questions)


def test_other_page_types_have_no_shape_rules():
    validate_page_shape("repo_overview", _page(opening="This subsystem is a boundary layer."))


async def test_a_page_over_its_shape_twice_is_kept_not_lost(sample_config):
    """Soft, unlike the artifact rules: a page over budget still reads."""
    jargon = _page(opening="This subsystem guards the boundary; see the child page.")
    provider = MockProvider(
        responses=[GeneratedResponse(content=jargon, input_tokens=1, output_tokens=1)]
    )
    generator = PageGenerator(provider, ContextAssembler(sample_config), sample_config)

    response = await generator._call_provider("module_page", "Document this.", "req")

    assert provider.call_count == 2
    assert response.content == jargon
    assert "abstract vocabulary" in provider.calls[1]["user_prompt"]
    # The re-ask names the rule, never the words it bans.
    assert "boundary" not in provider.calls[1]["user_prompt"].split("Reason:")[1]


# ---------------------------------------------------------------------------
# Deterministic repairs
# ---------------------------------------------------------------------------


def test_markdown_links_are_unwrapped_outside_code():
    content = "See [`core/a.py`](core/a.py) and ![logo](x.png).\n```\n[k](v)\n```\n"
    assert unwrap_markdown_links(content) == "See `core/a.py` and ![logo](x.png).\n```\n[k](v)\n```\n"


def test_links_inside_inline_code_and_web_links_are_kept():
    content = "Call `handlers[name](event)`; read [the guide](https://example.org/a).\n"
    assert unwrap_markdown_links(content) == content


def test_the_neighbours_section_comes_from_the_graph():
    facts = {
        "used_by": [{"area": "cli/", "imports": 4}],
        "depends_on": [{"area": "(root)", "imports": 1}],
    }
    assert connections_markdown(facts) == (
        "- Used by: `cli/`\n- Depends on: the repository root"
    )
    assert connections_markdown({}) is None


def test_the_neighbours_section_replaces_the_model_paragraph():
    page = "# P\n\n## How it connects\n\nWords the model wrote.\n\n## Later\n\nx\n"
    out = embed_section(page, "How it connects", "- Used by: `cli/`")
    assert "Words the model wrote" not in out
    assert out.count("## How it connects") == 1
    assert embed_section(page, "How it connects", None).count("How it connects") == 0


# ---------------------------------------------------------------------------
# Which flow a page draws
# ---------------------------------------------------------------------------


def _flow(entry: str, *, reach: int, score: float = 0.8, trace=None):
    return SimpleNamespace(
        entry_point_id=entry,
        entry_point_score=score,
        reach=reach,
        trace=trace or [entry],
    )


def test_flows_rank_by_how_much_code_they_drive():
    niche = _flow("cli/restyle.py::restyle", reach=90, score=0.80)
    main = _flow("cli/update.py::update", reach=500, score=0.49)
    assert [f.entry_point_id for f in rank_by_importance([niche, main])] == [
        "cli/update.py::update",
        "cli/restyle.py::restyle",
    ]
