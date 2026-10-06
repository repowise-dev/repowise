"""Every file a module page cites exists and is what its section talks about."""

from __future__ import annotations

from repowise.core.generation.page_sources import lint_sources

_MEMBERS = [
    "src/shop/orders/place.py",
    "src/shop/orders/models.py",
    "src/shop/billing/charge.py",
    "src/shop/config.py",
]
_KNOWN = [*_MEMBERS, "src/web/config.py", "src/web/views.py"]
_SYMBOLS = {"src/shop/billing/charge.py": ["charge_card"]}


def _lint(content: str) -> str:
    return lint_sources(
        content, base="src/shop", members=_MEMBERS, known_paths=_KNOWN, symbols_by_file=_SYMBOLS
    )


def _page(section: str, sources: str) -> str:
    return f"# Shop\n\nOpening.\n\n## Reading the basket\n\n{section}\n\nSources: {sources}\n"


def test_a_named_existing_file_is_kept():
    out = _lint(_page("`place.py` checks the basket first.", "`orders/place.py`"))
    assert "Sources: `orders/place.py`" in out


def test_a_missing_file_is_dropped_and_an_empty_line_goes_with_it():
    out = _lint(_page("The planner checks the basket.", "`orders/planner.py`"))
    assert "Sources" not in out
    assert "The planner checks the basket." in out


def test_a_file_the_section_never_mentions_is_dropped():
    out = _lint(_page("`place.py` checks the basket.", "`orders/place.py`, `orders/models.py`"))
    assert "Sources: `orders/place.py`" in out
    assert "models.py" not in out.split("Sources:")[1]


def test_a_symbol_or_the_part_name_is_enough():
    out = _lint(_page("It calls `charge_card` on the way out.", "`billing/charge.py`"))
    assert "Sources: `billing/charge.py`" in out
    out = _lint(_page("Billing takes the payment.", "`billing/charge.py`"))
    assert "Sources: `billing/charge.py`" in out


def test_a_short_stem_matches_whole_words_only():
    out = _lint(_page("Configuration-specific rules apply.", "`config.py`"))
    assert "Sources" not in out


def test_a_bare_name_resolves_to_the_member_and_is_rewritten():
    # Two files are called config.py; the module's own one wins.
    out = _lint(_page("`config.py` holds the defaults.", "config.py"))
    assert "Sources: `config.py`" in out
    out = _lint(_page("`charge.py` settles it.", "`charge.py`"))
    assert "Sources: `billing/charge.py`" in out


def test_a_directory_can_be_cited():
    out = _lint(_page("The orders part owns the flow.", "`orders/`"))
    assert "Sources: `orders/`" in out


def test_start_reading_drops_a_file_that_does_not_exist():
    content = (
        "# Shop\n\n## Where to start reading\n\n"
        "- `orders/place.py` - the entry.\n- `orders/ghost.py` - invented.\n"
        "- `src/shop/config.py` - defaults.\n"
    )
    out = _lint(content)
    assert "- `orders/place.py` - the entry." in out
    assert "ghost" not in out
    assert "- `config.py` - defaults." in out


def test_text_outside_sources_lines_is_untouched():
    page = _page("`place.py` checks the basket.", "`orders/place.py`")
    assert _lint(page) == page


def test_a_file_named_by_its_full_path_is_kept():
    out = _lint(_page("`src/shop/billing/charge.py` settles the card.", "`charge.py`"))
    assert "Sources: `billing/charge.py`" in out
    out = _lint(_page("`billing/charge.py` settles the card.", "`billing/charge.py`"))
    assert "Sources: `billing/charge.py`" in out
