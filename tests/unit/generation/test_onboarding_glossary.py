"""The glossary page: the words a repository uses for itself, defined by it.

Every assertion here is about the same property. A glossary is the page a
reader consults *because* they do not know the answer, so they cannot catch a
wrong one. Nothing on it may be written — the term, the sentence and the path
are all quoted, and a term the repository never defined renders blank rather
than plausibly.
"""

from __future__ import annotations

import jinja2
import pytest
from structlog.testing import capture_logs

from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.house_vocabulary import cell
from repowise.core.generation.onboarding import get_spec
from repowise.core.generation.onboarding.signals import OnboardingSignals
from repowise.core.generation.onboarding.slots import SLOT_GLOSSARY
from repowise.core.generation.onboarding.subkinds.glossary import _build
from repowise.core.generation.page_generator.structural import oneline
from repowise.core.ingestion.models import RepoStructure

#: What the structural side calls the parts of the system. Cut from the
#: dependency graph and named from the code, with no knowledge of the
#: documents — which is what makes a match between the two mean something.
MODULES = [
    "Dead Code and Reachability Analysis\nFinds files no import path reaches.",
    "Blast Radius UI\nRenders the blast radius of a pending change.",
    "Change Risk Analysis\nScores a diff before it merges.",
    "Ingestion Pipeline\nWalks the tree and parses each file.",
    "Knowledge Graph Routes\nServes the knowledge graph to the web reader.",
    "Split File Refactoring\nProposes a split for an overlong file.",
]


def _term(
    term: str,
    *,
    definition: str | None = None,
    definition_source: str | None = None,
    source_paths: tuple[str, ...] = ("README.md",),
    doc_frequency: int = 1,
    is_indexed_symbol: bool = False,
) -> HouseTerm:
    return HouseTerm(
        term=term,
        definition=definition,
        definition_source=definition_source,
        source_paths=source_paths,
        doc_frequency=doc_frequency,
        code_frequency=5,
        is_indexed_symbol=is_indexed_symbol,
    )


#: Six terms every one of ``MODULES`` corroborates, enough to clear the gate.
SIX_TERMS = [
    _term("Dead code", definition="Dead code is code no import path reaches."),
    _term("Blast radius", definition="Blast radius is what a change can reach."),
    _term("Change risk", definition="Change risk scores a diff before it merges."),
    _term("Ingestion pipeline", definition="Ingestion pipeline walks and parses the tree."),
    _term("Knowledge graph", definition="Knowledge graph holds the system's entities."),
    _term("Split file", definition="Split file proposes a split for a long file."),
]


def _signals(terms, modules=MODULES) -> OnboardingSignals:
    return OnboardingSignals(
        repo_name="ledger",
        repo_structure=RepoStructure(
            is_monorepo=False,
            packages=[],
            root_language_distribution={"python": 1.0},
            total_files=1,
            total_loc=10,
            entry_points=[],
        ),
        parsed_files=(),
        source_map={},
        graph_builder=None,
        pagerank={},
        betweenness={},
        community={},
        sccs=(),
        house_terms=tuple(terms),
        module_corroboration=tuple(modules),
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_repository_with_enough_vocabulary_gets_a_page():
    ctx = _build(_signals(SIX_TERMS))
    assert ctx is not None
    assert len(ctx.entries) == 6


def test_four_terms_is_not_a_glossary():
    """Below the floor the page is a handful of rows pretending to be a
    vocabulary, so it does not build at all."""
    assert _build(_signals(SIX_TERMS[:4])) is None


def test_the_skip_is_logged_with_the_counts_that_explain_it():
    """A page that stops appearing must say why. The two counts separate
    "nothing was mined" from "nothing the structure confirmed"."""
    with capture_logs() as logs:
        _build(_signals(SIX_TERMS[:4]))
    event = next(e for e in logs if e["event"] == "onboarding.glossary_gate_skipped")
    assert event["mined"] == 4
    assert event["corroborated"] == 4
    assert event["required"] == 5


def test_a_repository_that_mined_nothing_gets_no_page():
    assert _build(_signals([])) is None


def test_terms_no_module_group_corroborates_do_not_reach_the_page():
    """The corroboration is the whole selection rule. Vocabulary the documents
    use and the code never confirms is marketing, not a glossary entry."""
    assert _build(_signals(SIX_TERMS, modules=["Something Else\nUnrelated."])) is None


# ---------------------------------------------------------------------------
# What a row may say
# ---------------------------------------------------------------------------


def test_a_term_the_repository_never_defined_is_left_blank():
    """The one thing this page refuses to do. An invented definition on a
    glossary is unfalsifiable by the reader who came to look it up."""
    terms = [*SIX_TERMS[:5], _term("Split file")]
    ctx = _build(_signals(terms))
    entry = next(e for e in ctx.entries if e.term == "Split file")
    assert entry.definition is None
    assert entry.source_path == "README.md"


def test_a_definition_that_never_names_its_term_is_still_taken():
    """A heading gloss does not restate its own heading, and it is the
    commonest definition shape there is.

    Requiring the term to appear was built and measured and dropped. It caught
    two junk rows on this repository — "Related" glossed as "The performance
    pillar has its own, separate benchmark" — and it deleted "## Blast radius"
    over "The set of files a change can reach", along with every definition
    mined from a bolded lead-in, which captures only the text after the dash.
    A repository that documents in that style would have lost its whole
    definition column, on the front page as well as here.
    """
    terms = [
        *SIX_TERMS[:5],
        _term("Split file", definition="The file-level analog of Extract Class."),
    ]
    ctx = _build(_signals(terms))
    assert next(e for e in ctx.entries if e.term == "Split file").definition == (
        "The file-level analog of Extract Class."
    )


def test_a_command_line_is_not_a_definition():
    terms = [
        *SIX_TERMS[:5],
        _term("Split file", definition="repowise split-file --json # list them"),
    ]
    ctx = _build(_signals(terms))
    assert next(e for e in ctx.entries if e.term == "Split file").definition is None


def test_an_undefined_term_still_earns_its_row():
    """Corroboration is the only test a term has to pass.

    A row whose middle column is an em dash still carries two facts a reader
    came for: the repository has a word for this, and these are the parts of
    the system that use it. Requiring a definition was measured and reverted —
    on this repository it removed "Workspace", "Coupling", "CLI", "Distill",
    "Costs" and "Decisions", and on django it removed "Security", to remove
    two weak rows. A lookup page is judged on coverage.
    """
    ctx = _build(_signals([*SIX_TERMS, _term("Ingestion")]))
    listed = {e.term for e in ctx.entries}
    assert "Ingestion" in listed
    assert next(e for e in ctx.entries if e.term == "Ingestion").definition is None


def test_every_row_cites_a_real_path():
    ctx = _build(_signals(SIX_TERMS))
    assert all(e.source_path for e in ctx.entries)


def test_the_source_is_the_document_the_sentence_came_from():
    terms = [
        *SIX_TERMS[:5],
        _term(
            "Split file",
            definition="Split file proposes a split.",
            definition_source="docs/refactoring.md",
            source_paths=("README.md",),
        ),
    ]
    ctx = _build(_signals(terms))
    assert next(e for e in ctx.entries if e.term == "Split file").source_path == (
        "docs/refactoring.md"
    )


def test_a_row_names_the_parts_of_the_system_that_use_it():
    ctx = _build(_signals(SIX_TERMS))
    entry = next(e for e in ctx.entries if e.term == "Dead code")
    assert "Dead Code and Reachability Analysis" in entry.used_in


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------


def test_rows_are_alphabetical():
    """A glossary is looked up rather than read. Ranking decided which terms
    are here; it has no job left once they are."""
    ctx = _build(_signals(SIX_TERMS))
    terms = [e.term for e in ctx.entries]
    assert terms == sorted(terms, key=str.lower)


def test_two_builds_of_one_repository_agree():
    """An unstable glossary churns ``source_hash`` on every index, which
    re-embeds and re-persists a page that did not change."""
    assert _build(_signals(SIX_TERMS)) == _build(_signals(SIX_TERMS))


def test_the_order_of_the_corroboration_corpus_does_not_change_the_page():
    assert _build(_signals(SIX_TERMS)) == _build(_signals(SIX_TERMS, modules=MODULES[::-1]))


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@pytest.fixture
def render():
    from pathlib import Path

    import repowise.core.generation as generation

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(Path(generation.__file__).parent / "templates")),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    env.filters["oneline"] = oneline
    env.filters["table_cell"] = cell

    def _render(ctx):
        return env.get_template("stub/onboarding/glossary.j2").render(
            ctx=ctx, slot=SLOT_GLOSSARY
        )

    return _render


def test_the_page_renders_one_row_per_term(render):
    page = render(_build(_signals(SIX_TERMS)))
    rows = [line for line in page.splitlines() if line.startswith("| ")]
    # Six terms plus the header row. The alignment row opens ``|-``.
    assert len(rows) == 7
    assert "{{" not in page


def test_an_undefined_term_renders_an_em_dash_not_a_sentence(render):
    page = render(_build(_signals([*SIX_TERMS[:5], _term("Split file")])))
    row = next(line for line in page.splitlines() if line.startswith("| **Split file**"))
    assert "| — |" in row


def test_a_pipe_in_mined_prose_does_not_break_the_table(render):
    """Definitions are the repository's text, not ours. One quoting a shell
    pipeline would otherwise shift every column to its right."""
    terms = [
        *SIX_TERMS[:5],
        _term("Split file", definition="Split file findings pipe | into the report."),
    ]
    page = render(_build(_signals(terms)))
    row = next(line for line in page.splitlines() if line.startswith("| **Split file**"))
    assert row.count("|") - row.count("\\|") == 5


def test_a_term_the_codebase_defines_is_backticked_and_a_coined_one_is_not(render):
    """``check_grounding`` strips backticks off any token it cannot resolve to
    a symbol, so backticking a coined term is a silent failure waiting to
    happen. This page is deterministic and never passes through that check —
    which is exactly why it has to get the distinction right itself."""
    terms = [
        *SIX_TERMS[:5],
        _term("SplitFile", definition="SplitFile proposes a split.", is_indexed_symbol=True),
    ]
    modules = [*MODULES, "Split File Refactoring\nThe SplitFile detector runs here."]
    page = render(_build(_signals(terms, modules=modules)))
    assert "| `SplitFile` |" in page
    assert "| **Dead code** |" in page


def test_the_page_says_how_much_of_its_vocabulary_it_could_define(render):
    """A glossary covering a third of the terms it lists should say so rather
    than reading as though the repository defined everything."""
    page = render(_build(_signals([*SIX_TERMS[:5], _term("Split file")])))
    assert "5 of 6 terms carry a definition" in page


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_glossary_is_registered_and_needs_no_model():
    """Enumerable facts written by a model are resampled on every render. The
    whole page is enumerable facts, so no model is in its path."""
    spec = get_spec(SLOT_GLOSSARY)
    assert spec is not None
    assert spec.deterministic is True
    assert spec.needs_module_corroboration is True
    assert spec.title == "Glossary"


def test_every_subkind_has_the_templates_its_flags_promise():
    """The flag and the files have to agree, and nothing else checks that.

    A ``deterministic`` subkind renders only its ``stub/`` template, so it is
    allowed to ship without a prompt. Every other subkind needs both, and
    losing either is a ``TemplateNotFound`` at generation time on a repository
    that is not ours — the suite is where that has to fail instead.
    """
    from pathlib import Path

    import repowise.core.generation as generation
    from repowise.core.generation.onboarding import iter_specs

    templates = Path(generation.__file__).parent / "templates"
    for spec in iter_specs():
        stub = templates / "stub" / "onboarding" / spec.template
        assert stub.is_file(), f"{spec.slot} has no stub template at {stub}"
        prompt = templates / "onboarding" / spec.template
        if spec.deterministic:
            assert not prompt.is_file(), (
                f"{spec.slot} is deterministic, so its prompt template at "
                f"{prompt} is dead weight that no run can reach"
            )
        else:
            assert prompt.is_file(), f"{spec.slot} has no prompt template at {prompt}"


def test_a_deterministic_subkind_reaches_the_no_provider_path():
    """The flag has to be read, not merely set.

    ``generate_onboarding_page`` branches on it, and the glossary has no prompt
    template at all — so if that branch is ever dropped the page raises
    ``TemplateNotFound`` in production while every assertion about the spec
    still passes.
    """
    import inspect

    from repowise.core.generation.page_generator import pertype

    source = inspect.getsource(pertype.PerTypeGenerationMixin.generate_onboarding_page)
    assert "spec.deterministic" in source
    assert "_model_free_onboarding_page" in source


# ---------------------------------------------------------------------------
# Honesty about what is not on the page
# ---------------------------------------------------------------------------


#: Word-only stems, because a term's words are what the corroboration pattern
#: matches — a digit in the name matches no module group and corroborates
#: nothing.
_STEMS = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu", "amber", "bronze", "copper", "diamond",
    "emerald", "flint", "garnet", "ivory", "jade", "kevlar", "lapis",
    "marble", "nickel", "onyx", "pearl", "quartz", "ruby", "slate", "topaz",
    "umber", "violet", "willow", "xenon", "yarrow", "zircon", "almond",
    "birch", "cedar", "dogwood",
]


def _many(count: int):
    """*count* distinct two-word terms, and a module group naming each."""
    stems = _STEMS[:count]
    assert len(stems) == count, "not enough distinct stems for this test"
    terms = [
        _term(f"{s.title()} code", definition=f"{s.title()} code is a rule nothing reaches.")
        for s in stems
    ]
    modules = [f"{s.title()} Code Analysis\nFinds {s} code." for s in stems]
    return terms, modules


def test_a_long_vocabulary_is_capped_and_says_so(render):
    """The footer explains that a term reaches this page only with structural
    corroboration. That is a false account of a term dropped for length, so the
    count before the cap is carried onto the page too."""
    terms, modules = _many(55)
    ctx = _build(_signals(terms, modules=modules))
    assert ctx.corroborated == 55
    assert len(ctx.entries) == 40

    page = render(ctx)
    assert "The 40 most corroborated are listed; 15 more were left off" in page
    # Nothing failed corroboration here, so the page does not claim anything did.
    assert "candidate terms were mined in all" not in page


def test_a_page_listing_everything_claims_no_truncation(render):
    page = render(_build(_signals(SIX_TERMS)))
    assert "left off for length" not in page


def test_a_row_says_when_its_subsystem_list_is_cut(render):
    """"Where it is used" is the column a reader acts on, and a truncated list
    reads as the whole list."""
    modules = [
        *MODULES,
        "Dead Code Sweeper\nRuns the dead code pass nightly.",
        "Dead Code Report\nRenders the dead code findings.",
        "Dead Code API\nServes dead code to the web reader.",
    ]
    ctx = _build(_signals(SIX_TERMS, modules=modules))
    entry = next(e for e in ctx.entries if e.term == "Dead code")
    assert entry.used_in[-1] == "and 1 more"
    assert "and 1 more" in render(ctx)


# ---------------------------------------------------------------------------
# The page is finished, not a stub
# ---------------------------------------------------------------------------


def test_the_rendered_page_does_not_open_a_setext_heading(render):
    """``paragraph`` followed by ``---`` is a setext H2 in CommonMark, not a
    thematic break. Without a blank line between them the closing note typesets
    the paragraph above it as a section heading."""
    long_terms, long_modules = _many(55)
    contexts = (_build(_signals(SIX_TERMS)), _build(_signals(long_terms, modules=long_modules)))
    for ctx in contexts:
        page = render(ctx)
        rule = page.index("\n---\n")
        assert page[rule - 1] == "\n", "the thematic break has no blank line above it"


def test_two_renders_of_one_repository_are_byte_identical(render):
    """The page's own closing claim, asserted on the bytes rather than on the
    context object."""
    first = render(_build(_signals(SIX_TERMS)))
    second = render(_build(_signals(SIX_TERMS)))
    assert first == second


def test_a_model_free_page_is_not_counted_as_unwritten():
    """It carries ``provider_name='template'`` like a stub and means the
    opposite by it.

    Counted as unwritten, the glossary is offered to ``generate --unwritten``
    on every run, billed in the cost estimate for prose that will not be
    written, and leaves the reader UI's bulk-generate affordance up on a wiki
    that is complete.
    """
    import json
    from types import SimpleNamespace

    from repowise.core.generation.scope import load_page_records

    def _page(page_id, meta):
        return SimpleNamespace(
            id=page_id,
            page_type="onboarding",
            target_path=f"onboarding/{page_id}",
            provider_name="template",
            metadata_json=json.dumps(meta),
            freshness_status="fresh",
        )

    records = {
        r.page_id: r
        for r in load_page_records(
            [
                _page("glossary", {"subkind": "glossary", "model_free": True}),
                _page("key_concepts", {"subkind": "key_concepts"}),
            ]
        )
    }
    assert records["glossary"].is_template is False
    # An ordinary subkind on a stub is still unwritten, and still offered.
    assert records["key_concepts"].is_template is True
