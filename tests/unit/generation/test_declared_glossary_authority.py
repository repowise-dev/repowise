"""A glossary the team authored is the authority on naming (#2262).

Repowise already mines a repository's house vocabulary and renders it as the
onboarding Glossary page. That path is descriptive — what the repository says
its words mean — and it is measured, deterministic and cited, so nothing here
changes it. This is the prescriptive half: a file a person wrote down naming
one canonical word per concept and listing the synonyms to avoid.

Every assertion is about *authority*, which is the property the issue is
about. A term from that file beats a mined term that spells the same phrase; it
keeps its row when no code corroborates it, because in DDD that gap is the
signal; a word the file marks avoided is demoted rather than deleted; and the
generated pages and agent instructions carry the canonical word rather than the
one the repository happens to write today.

The negative cases carry as much weight as the positive ones. A repository with
no declared glossary must produce *exactly* what it produced before, byte for
byte, or the change has quietly altered every existing user's wiki.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.declared_glossary import (
    DeclaredTerm,
    declared_avoid_index,
    load_declared_glossary,
    parse_declared_glossary,
    phrase_key,
)
from repowise.core.generation.house_vocabulary import select_terms

# ---------------------------------------------------------------------------
# The glossary format
# ---------------------------------------------------------------------------
# The shape the ``grill-with-docs`` skill writes into a root ``CONTEXT.md``:
# a bolded term, the team's own sentence, and an ``_Avoid_`` line. Plus the
# table form, which is what a team that kept a glossary before they had a skill
# writing one already has.

_POCOCK = """\
# Ordering

How orders are taken and fulfilled.

## Language

**Order**:
A customer's request to purchase one or more products.
_Avoid_: Purchase, transaction

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer, account

**Co-change**:

## Relationships

- **Ordering -> Fulfillment**: Ordering emits `OrderPlaced` events.
"""

_TABLE = """\
# Glossary

| Term | Definition | Avoid |
|---|---|---|
| Invoice | A request for payment sent after delivery. | Bill, payment request |
| Ledger | The book of record. |  |

| Something else entirely |
|---|
| not read as a term |
"""


def test_the_pocock_format_is_read_whole() -> None:
    terms = parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")
    assert [t.term for t in terms] == ["Order", "Customer", "Co-change"]


def test_a_definition_is_the_team_s_own_sentence() -> None:
    """Quoted, never paraphrased. The author is the authority on their word."""
    by_term = {t.term: t for t in parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")}
    assert by_term["Order"].definition == (
        "A customer's request to purchase one or more products."
    )
    assert by_term["Order"].avoid == ("Purchase", "transaction")


def test_a_second_avoid_line_adds_to_the_first() -> None:
    """A wrapped ``_Avoid_`` list must not be silently halved."""
    text = "**Order**:\nA request.\n_Avoid_: Purchase\n_Avoid_: transaction\n"
    terms = parse_declared_glossary(text, source_path="CONTEXT.md")
    assert terms[0].avoid == ("Purchase", "transaction")


def test_a_defined_term_with_no_avoid_line_has_none() -> None:
    by_term = {t.term: t for t in parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")}
    assert by_term["Co-change"].avoid == ()


def test_a_term_with_no_sentence_reports_none() -> None:
    """The same refusal the miner makes: a term without a definition is
    ``None`` rather than a plausible one, on both halves of the glossary."""
    by_term = {t.term: t for t in parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")}
    assert by_term["Co-change"].definition is None


def test_the_definition_stops_at_the_term_s_own_paragraph() -> None:
    """A second paragraph is a second thought, not more of the definition.

    Running on is how a term ends up defined by the section below it, which
    reads as authoritative and is about something else.
    """
    text = "**Order**:\nA request.\n\nUnrelated prose about the weather.\n"
    assert parse_declared_glossary(text, source_path="CONTEXT.md")[0].definition == "A request."


def test_each_term_records_the_line_it_was_written_on() -> None:
    """A citation a reader can open at the sentence, not at the document."""
    by_term = {t.term: t for t in parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")}
    assert by_term["Order"].line == 7
    assert by_term["Order"].source_path == "CONTEXT.md"


def test_the_table_fallback_is_read() -> None:
    """A team that already kept a glossary should not be told it does not
    count. Its rows are the same three columns by another spelling."""
    terms = parse_declared_glossary(_TABLE, source_path="docs/GLOSSARY.md")
    assert [t.term for t in terms] == ["Invoice", "Ledger"]
    assert terms[0].avoid == ("Bill", "payment request")
    assert terms[0].definition == "A request for payment sent after delivery."


def test_a_row_outside_the_glossary_table_is_not_a_term() -> None:
    """The second table in the fixture has one column and no header. Reading
    it would turn an unrelated table into vocabulary."""
    terms = parse_declared_glossary(_TABLE, source_path="docs/GLOSSARY.md")
    assert "Something else entirely" not in {t.term for t in terms}


def test_a_relationship_line_in_a_context_map_is_not_a_term() -> None:
    """``- **Ordering -> Fulfillment**: ...`` is a relationship, and the arrow
    and the list marker are what say so."""
    terms = parse_declared_glossary(_POCOCK, source_path="CONTEXT.md")
    assert all("->" not in t.term for t in terms)
    assert "Ordering -> Fulfillment" not in {t.term for t in terms}


def test_a_term_inside_a_fenced_block_is_not_read() -> None:
    """Documentation shows the format by example, and the example is not a
    declaration."""
    text = "**Real**:\nA thing.\n\n```md\n**Example**:\nNot a term.\n```\n"
    assert [t.term for t in parse_declared_glossary(text, source_path="x.md")] == ["Real"]


def test_a_bolded_phrase_mid_sentence_is_prose() -> None:
    text = "Use the **Order** word when you mean it.\n"
    assert parse_declared_glossary(text, source_path="x.md") == []


def test_a_duplicate_term_is_kept_once() -> None:
    """First spelling wins, the same rule the miner applies."""
    text = "**Order**:\nA request.\n\n**order**:\nAnother spelling.\n"
    terms = parse_declared_glossary(text, source_path="x.md")
    assert [t.term for t in terms] == ["Order"]
    assert terms[0].definition == "A request."


def test_an_inline_definition_is_read() -> None:
    text = "**Order**: A customer's request to purchase something.\n_Avoid_: Purchase\n"
    terms = parse_declared_glossary(text, source_path="x.md")
    assert terms[0].definition == "A customer's request to purchase something."
    assert terms[0].avoid == ("Purchase",)


def test_the_phrase_key_folds_the_gap_between_words() -> None:
    """``Co-change``, ``co_change`` and ``Co change`` are one term.

    This is the same equivalence ``phrase_pattern`` uses to match a term
    against prose, so a declared term and a mined term that fold to the same
    key are the same word.
    """
    assert phrase_key("Co-change") == phrase_key("co_change") == phrase_key("Co change")


# ---------------------------------------------------------------------------
# Loading the file off disk
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path, *, root: str | None = None, docs: bool = False) -> Path:
    root_dir = tmp_path / "ordering"
    (root_dir / "src").mkdir(parents=True)
    (root_dir / "src" / "engine.py").write_text(
        '"""Order handling for the ledger."""\n', encoding="utf-8"
    )
    if root is not None:
        (root_dir / "CONTEXT.md").write_text(root, encoding="utf-8")
    if docs:
        glossary = root_dir / "docs"
        glossary.mkdir()
        (glossary / "GLOSSARY.md").write_text(_TABLE, encoding="utf-8")
    return root_dir


def test_a_root_context_file_is_found(tmp_path: Path) -> None:
    root = _repo(tmp_path, root=_POCOCK)
    terms = load_declared_glossary(root)
    assert [t.term for t in terms] == ["Order", "Customer", "Co-change"]


def test_docs_glossary_is_found(tmp_path: Path) -> None:
    root = _repo(tmp_path, docs=True)
    assert {t.term for t in load_declared_glossary(root)} == {"Invoice", "Ledger"}


def test_a_repository_with_no_declared_glossary_returns_nothing(tmp_path: Path) -> None:
    """The common case. Not a failure, and not a warning either."""
    assert load_declared_glossary(_repo(tmp_path)) == []


def test_a_context_map_names_the_per_context_files(tmp_path: Path) -> None:
    """Multi-context repositories keep one ``CONTEXT.md`` per bounded context,
    and the map is what says where they are and what each one is called."""
    root = _repo(tmp_path)
    billing = root / "src" / "billing"
    billing.mkdir()
    (billing / "CONTEXT.md").write_text(
        "**Invoice**:\nA request for payment sent after delivery.\n_Avoid_: Bill\n",
        encoding="utf-8",
    )
    (root / "CONTEXT-MAP.md").write_text(
        "# Context Map\n\n## Contexts\n\n"
        "- [Billing](./src/billing/CONTEXT.md): generates invoices\n",
        encoding="utf-8",
    )
    terms = load_declared_glossary(root)
    assert [t.term for t in terms] == ["Invoice"]
    assert terms[0].context == "Billing"
    assert terms[0].source_path == "src/billing/CONTEXT.md"


def test_a_map_entry_pointing_at_a_missing_file_costs_only_that_file(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path, root=_POCOCK)
    (root / "CONTEXT-MAP.md").write_text(
        "- [Gone](./src/gone/CONTEXT.md): not committed\n", encoding="utf-8"
    )
    assert len(load_declared_glossary(root)) == 3


def test_a_git_excluded_glossary_is_not_read(tmp_path: Path) -> None:
    """A path the repository excludes from its own index is scratch work, not a
    declaration. The same rule the miner applies to its sources."""
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("local-stash/\n", encoding="utf-8")
    stash = root / "local-stash"
    stash.mkdir()
    (stash / "GLOSSARY.md").write_text(
        "**Phantom**:\nWhatever this throwaway says.\n", encoding="utf-8"
    )
    assert load_declared_glossary(root) == []


def test_a_binary_or_unreadable_file_does_not_raise(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "CONTEXT.md").write_bytes(b"\xff\xfe\x00\x01not text")
    load_declared_glossary(root)  # must not raise


def test_loading_is_deterministic(tmp_path: Path) -> None:
    root = _repo(tmp_path, root=_POCOCK, docs=True)
    assert load_declared_glossary(root) == load_declared_glossary(root)


# ---------------------------------------------------------------------------
# Authority: the declared term wins
# ---------------------------------------------------------------------------
# The heart of the issue. A mined term and a declared term spelling the same
# phrase are one concept, and the declared one is what a reader is meant to
# write. They must not both appear: listing both presents a choice the team
# already made.

MODULES = [
    "Order Handling\nTakes orders and stores them.",
    "Ledger Postings\nWrites every order to the ledger.",
]
#: A corpus where the code spells the *avoided* word. This is the shape the
#: demotion rule exists for: the team agreed on "Order", the codebase says
#: "Purchase", and the disagreement is worth a row rather than silence.
MODULES_WITH_AVOIDED = [
    *MODULES,
    "Purchase Records\nStores every purchase a customer makes.",
]


def _mined(term: str, *, definition: str | None = None) -> HouseTerm:
    return HouseTerm(
        term=term,
        definition=definition,
        definition_source="README.md" if definition else None,
        source_paths=("README.md",),
        doc_frequency=1,
        code_frequency=3,
        is_indexed_symbol=False,
    )


def _declared(term: str, **kwargs: object) -> DeclaredTerm:
    defaults: dict[str, object] = {
        "definition": None,
        "avoid": (),
        "context": None,
        "source_path": "CONTEXT.md",
        "line": 1,
    }
    defaults.update(kwargs)
    return DeclaredTerm(term=term, **defaults)  # type: ignore[arg-type]


def test_a_declared_term_wins_over_the_mined_one_spelling_it() -> None:
    """The one assertion the issue is really about."""
    selected = select_terms(
        [_mined("Order", definition="An order is a row in the orders table.")],
        MODULES,
        declared=[_declared("Order", definition="A customer's request to purchase.")],
    )
    assert [t.term for t in selected] == ["Order"]
    assert selected[0].status == "declared"
    assert selected[0].definition == "A customer's request to purchase."
    assert selected[0].source_path == "CONTEXT.md"


def test_the_same_phrase_under_another_spelling_is_the_same_concept() -> None:
    """``co-change`` in the code and ``Co-change`` in the glossary are one
    term, and folding them is what stops the page listing the concept twice."""
    selected = select_terms(
        [_mined("co_change")],
        MODULES,
        declared=[_declared("Co-change", definition="Two files landing together.")],
    )
    assert [t.term for t in selected] == ["Co-change"]
    assert selected[0].status == "declared"


def test_a_declared_term_no_code_corroborates_still_reaches_the_page() -> None:
    """The carve-out. ``select_terms`` drops a mined term with no corroboration
    ("bind-or-drop"), and that rule is right for a word a document invented.
    A word the team agreed on and the code has not caught up to is the signal,
    not noise, so it keeps its row with no corroboration at all."""
    selected = select_terms(
        [],
        ["Something Else Entirely\nUnrelated."],
        declared=[_declared("Bounded Context", definition="Where a term means one thing.")],
    )
    assert [t.term for t in selected] == ["Bounded Context"]
    assert selected[0].corroborating_pages == 0
    assert selected[0].corroborating_names == ()


def test_a_declared_term_that_is_in_the_code_says_where() -> None:
    """Corroboration is still computed. "Where is this used" is the column a
    reader acts on, and a declared term the code spells should answer it."""
    selected = select_terms(
        [_mined("Order")],
        MODULES,
        declared=[_declared("Order", definition="A request to purchase.")],
    )
    assert selected[0].corroborating_names == ("Ledger Postings", "Order Handling")


def test_a_declared_term_leads_the_page() -> None:
    """Order is the ranking rule: the team's words, then whatever the structure
    confirmed, then whatever the glossary marks wrong."""
    selected = select_terms(
        [_mined("Ledger")],
        MODULES,
        declared=[_declared("Order", definition="A request to purchase.")],
    )
    assert [t.status for t in selected] == ["declared", "mined"]


def test_the_declared_avoid_list_reaches_the_row() -> None:
    selected = select_terms(
        [],
        MODULES,
        declared=[_declared("Order", avoid=("Purchase", "transaction"))],
    )
    assert selected[0].avoid == ("Purchase", "transaction")


def test_the_context_reaches_the_row() -> None:
    selected = select_terms([], MODULES, declared=[_declared("Order", context="Billing")])
    assert selected[0].context == "Billing"


def test_a_declared_definition_is_never_put_through_the_miner_s_shape_test() -> None:
    """``is_definition`` exists to tell a sentence about a term from the shell
    fragment nearest it in mined prose. Applied to a ``CONTEXT.md`` it would
    delete a terse one-line definition for being terse, and the author is the
    authority on their own sentence."""
    selected = select_terms(
        [],
        MODULES,
        declared=[_declared("Order", definition="A purchase request.")],
    )
    assert selected[0].definition == "A purchase request."


# ---------------------------------------------------------------------------
# An avoided word is demoted, never deleted
# ---------------------------------------------------------------------------
# The row is evidence: the code says one thing and the team agreed on another.
# Dropping it silently is how a glossary becomes something nobody can audit, so
# it is kept, ranked last, and carries the word to use instead.


def test_a_mined_term_the_glossary_avoids_is_demoted_not_dropped() -> None:
    selected = select_terms(
        [_mined("Purchase", definition="Purchase records a transaction.")],
        MODULES_WITH_AVOIDED,
        declared=[_declared("Order", avoid=("Purchase",))],
    )
    assert [t.term for t in selected] == ["Order", "Purchase"]
    demoted = selected[1]
    assert demoted.demoted_by == "Order"
    assert demoted.status == "mined", "it is still what the code says"


def test_a_demoted_term_ranks_below_everything_else() -> None:
    selected = select_terms(
        [_mined("Purchase"), _mined("Ledger"), _mined("Order")],
        MODULES_WITH_AVOIDED,
        declared=[_declared("Order", avoid=("Purchase",))],
    )
    # "Order" is declared, so it leads; "Purchase" is what the code says and is
    # ranked last; the demoted row is after every row the team has not ruled on.
    assert [t.term for t in selected] == ["Order", "Ledger", "Purchase"]
    assert selected[-1].demoted_by == "Order"


def test_demotion_is_case_insensitive() -> None:
    """``purchase`` and ``Purchase`` are the same synonym to a reader."""
    selected = select_terms(
        [_mined("purchase")],
        MODULES_WITH_AVOIDED,
        declared=[_declared("Order", avoid=("Purchase",))],
    )
    assert selected[-1].demoted_by == "Order"


def test_an_avoided_phrase_the_code_never_uses_is_simply_absent() -> None:
    """Nothing to demote. A glossary may ban a word the repository never had."""
    selected = select_terms(
        [_mined("Ledger")],
        MODULES,
        declared=[_declared("Order", avoid=("Buy",))],
    )
    assert [t.term for t in selected] == ["Order", "Ledger"]
    assert all(t.demoted_by is None for t in selected)


def test_the_demotion_is_logged_with_the_canonical_word() -> None:
    with capture_logs() as logs:
        select_terms(
            [_mined("Purchase")],
            MODULES_WITH_AVOIDED,
            declared=[_declared("Order", avoid=("Purchase",))],
        )
    event = next(e for e in logs if e["event"] == "house_vocabulary.avoided_term_demoted")
    assert event["count"] == 1
    assert event["terms"] == ["Purchase"]
    assert event["canonical"] == ["Order"]


def test_the_avoid_index_folds_and_maps_to_the_canonical_spelling() -> None:
    index = declared_avoid_index(
        [
            _declared("Order", avoid=("Purchase", "transaction")),
            _declared("Customer", avoid=("Client",)),
        ]
    )
    assert index == {"purchase": "Order", "transaction": "Order", "client": "Customer"}


def test_the_selection_is_logged_with_both_counts() -> None:
    """A page that quietly stops carrying its declared rows should be visible
    from the log, not inferred from the page."""
    with capture_logs() as logs:
        select_terms(
            [_mined("Ledger")],
            MODULES,
            declared=[_declared("Order")],
        )
    event = next(e for e in logs if e["event"] == "house_vocabulary.selected")
    assert event["declared"] == 1
    assert event["mined"] == 1


# ---------------------------------------------------------------------------
# Nothing changes for a repository that has not declared a glossary
# ---------------------------------------------------------------------------
# The whole existing wiki has to come out identical. These pin it: with no
# ``declared`` argument the selection is what it always was, down to the order.


def test_no_declared_glossary_selects_exactly_as_before() -> None:
    house = [
        _mined("Order", definition="An order is a row."),
        _mined("Ledger"),
        _mined("Purchase"),
    ]
    corpus = MODULES_WITH_AVOIDED
    assert select_terms(house, corpus) == select_terms(house, corpus, declared=[])
    assert [t.term for t in select_terms(house, corpus)] == [
        "Order",
        "Ledger",
        "Purchase",
    ]
    assert all(t.status == "mined" for t in select_terms(house, corpus))
    assert all(not t.avoid for t in select_terms(house, corpus))
    assert all(t.demoted_by is None for t in select_terms(house, corpus))


def test_a_mined_term_with_no_corroboration_is_still_dropped() -> None:
    """The carve-out is for declared terms only. The mined gate is untouched."""
    assert select_terms([_mined("Unbuilt")], ["Nothing\nRelated."]) == []


def test_a_limit_still_slices_the_tail() -> None:
    house = [_mined("Order"), _mined("Ledger"), _mined("Purchase")]
    assert len(select_terms(house, MODULES, limit=2)) == 2


def test_a_declared_term_is_counted_in_the_limit() -> None:
    """The declared rows lead, so a small limit takes them first. That is the
    ranking rule rather than an accident of slice order."""
    selected = select_terms(
        [_mined("Ledger")],
        MODULES,
        declared=[_declared("Order")],
        limit=1,
    )
    assert [t.term for t in selected] == ["Order"]


# ---------------------------------------------------------------------------
# The glossary page
# ---------------------------------------------------------------------------


def _term_kwargs(term: str, **kwargs: object) -> HouseTerm:
    return _mined(term, **kwargs)  # type: ignore[arg-type]


def test_the_page_carries_the_declared_rows_with_their_columns() -> None:
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    ctx = _build_from(
        repo_name="ordering",
        house_terms=(_mined("Ledger"),),
        declared=(
            _declared(
                "Order",
                definition="A customer's request to purchase.",
                avoid=("Purchase",),
                context="Billing",
            ),
        ),
        module_corroboration=tuple(MODULES),
    )
    assert ctx is not None
    entry = next(e for e in ctx.entries if e.term == "Order")
    assert entry.status == "declared"
    assert entry.avoid == ("Purchase",)
    assert entry.context == "Billing"
    assert entry.source_path == "CONTEXT.md"
    assert ctx.declared == 1


def test_the_page_is_gated_at_five_terms_unless_a_glossary_is_declared() -> None:
    """The floor is bypassed when the team has declared one: they have already
    opted in, and refusing to render terms they deliberately wrote would be the
    tool overruling them."""
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    assert (
        _build_from(
            repo_name="ordering",
            house_terms=(_mined("Ledger"), _mined("Order")),
            declared=(),
            module_corroboration=tuple(MODULES),
        )
        is None
    )
    ctx = _build_from(
        repo_name="ordering",
        house_terms=(),
        declared=(_declared("Order", definition="A request."),),
        module_corroboration=tuple(MODULES),
    )
    assert ctx is not None
    assert [e.term for e in ctx.entries] == ["Order"]


def test_the_page_reports_which_declared_terms_the_code_does_not_spell() -> None:
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    ctx = _build_from(
        repo_name="ordering",
        house_terms=(),
        declared=(
            _declared("Order", definition="A request."),
            _declared("Bounded Context", definition="Where a term means one thing."),
        ),
        module_corroboration=tuple(MODULES),
    )
    assert ctx is not None
    # "Order" is spelled by a module group; "Bounded Context" is not, which is
    # the gap DDD treats as the signal.
    assert ctx.not_yet_in_code == 1
    assert ctx.declared == 2


def test_the_page_reports_demoted_rows() -> None:
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    ctx = _build_from(
        repo_name="ordering",
        house_terms=(_mined("Ledger"), _mined("Purchase")),
        declared=(_declared("Order", avoid=("Purchase",)),),
        module_corroboration=tuple(MODULES_WITH_AVOIDED),
    )
    assert ctx is not None
    assert ctx.demoted == 1
    entry = next(e for e in ctx.entries if e.term == "Purchase")
    assert entry.demoted_by == "Order"


def test_a_repository_with_neither_half_gets_no_page() -> None:
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    assert (
        _build_from(
            repo_name="ordering",
            house_terms=(),
            declared=(),
            module_corroboration=tuple(MODULES),
        )
        is None
    )


def test_the_page_is_stable_across_two_builds_with_a_declared_glossary() -> None:
    from repowise.core.generation.onboarding.subkinds.glossary import _build_from

    def build() -> object:
        return _build_from(
            repo_name="ordering",
            house_terms=(_mined("Ledger"),),
            declared=(_declared("Order", definition="A request.", avoid=("Purchase",)),),
            module_corroboration=tuple(MODULES),
        )

    assert build() == build()


# ---------------------------------------------------------------------------
# The front page: same selection, same authority
# ---------------------------------------------------------------------------


def test_the_capability_table_leads_with_the_declared_term() -> None:
    """A front page naming a synonym the glossary marks avoided is the exact
    failure this change is here to prevent, so both pages go through the same
    call."""
    from repowise.core.generation.overview_tables import select_capabilities

    picked = select_capabilities(
        [_mined("Purchase")],
        MODULES_WITH_AVOIDED,
        declared=[_declared("Order", definition="A request.", avoid=("Purchase",))],
        limit=6,
    )
    assert picked[0].term == "Order"
    assert picked[0].status == "declared"
    assert picked[-1].demoted_by == "Order"


def test_the_capability_table_is_unchanged_without_a_glossary() -> None:
    from repowise.core.generation.overview_tables import select_capabilities

    assert select_capabilities([_mined("Order")], MODULES, limit=6) == select_capabilities(
        [_mined("Order")], MODULES, limit=6, declared=[]
    )


# ---------------------------------------------------------------------------
# The agent instructions
# ---------------------------------------------------------------------------


def test_the_instruction_file_carries_the_canonical_terms(tmp_path: Path) -> None:
    """The cheapest way to make every agent speak the house language: the
    terms are in the prompt prefix of every session, so an agent that never
    calls a tool still knows which word to use."""
    from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
    from repowise.core.generation.editor_files.data import (
        CanonicalTerm,
        EditorFileData,
    )

    data = EditorFileData(
        repo_name="ordering",
        indexed_at="2026-09-18",
        indexed_commit="abc1234",
        architecture_summary="Takes orders.",
        canonical_terms=[
            CanonicalTerm(
                term="Order",
                definition="A customer's request to purchase.",
                avoid=("Purchase",),
                source_path="CONTEXT.md",
            )
        ],
    )
    rendered = AgentsMdGenerator().render(data)
    assert "### House vocabulary (use these words)" in rendered
    assert "**Order**" in rendered
    assert "avoid: Purchase" in rendered
    assert "do not write the avoided ones" in rendered


def test_the_instruction_file_omits_the_section_without_a_glossary() -> None:
    """Every existing user's CLAUDE.md has to come out identical, so the
    section must not render as an empty heading."""
    from repowise.core.generation.editor_files.agents_md import AgentsMdGenerator
    from repowise.core.generation.editor_files.data import EditorFileData

    data = EditorFileData(
        repo_name="ordering",
        indexed_at="2026-09-18",
        indexed_commit="abc1234",
        architecture_summary="Takes orders.",
    )
    assert "House vocabulary" not in AgentsMdGenerator().render(data)


def test_the_claude_md_carries_the_same_section() -> None:
    """Both hosts read one shared block, and the two have drifted apart before
    when the prose was written twice."""
    from repowise.core.generation.editor_files.claude_md import ClaudeMdGenerator
    from repowise.core.generation.editor_files.data import (
        CanonicalTerm,
        EditorFileData,
    )

    data = EditorFileData(
        repo_name="ordering",
        indexed_at="2026-09-18",
        indexed_commit="abc1234",
        architecture_summary="Takes orders.",
        canonical_terms=[CanonicalTerm(term="Order", avoid=("Purchase",))],
    )
    rendered = ClaudeMdGenerator().render(data)
    assert "### House vocabulary (use these words)" in rendered
    assert "**Order**" in rendered


@pytest.mark.asyncio
async def test_the_fetcher_reads_the_repository_s_own_glossary(tmp_path: Path) -> None:
    """Read from the checkout rather than the store: the file *is* the
    authority and is not persisted anywhere, so the instruction file and the
    glossary cannot drift apart."""
    from repowise.core.generation.editor_files.fetcher import EditorFileDataFetcher

    root = _repo(tmp_path, root=_POCOCK)
    fetcher = EditorFileDataFetcher(session=None, repo_id="r1", repo_path=root)  # type: ignore[arg-type]
    terms = fetcher._get_canonical_terms()
    assert [t.term for t in terms] == ["Order", "Customer", "Co-change"]
    assert terms[0].avoid == ("Purchase", "transaction")
    assert terms[0].source_path == "CONTEXT.md"


@pytest.mark.asyncio
async def test_the_fetcher_returns_nothing_for_a_repository_without_one(
    tmp_path: Path,
) -> None:
    from repowise.core.generation.editor_files.fetcher import EditorFileDataFetcher

    fetcher = EditorFileDataFetcher(  # type: ignore[arg-type]
        session=None, repo_id="r1", repo_path=_repo(tmp_path)
    )
    assert fetcher._get_canonical_terms() == []
