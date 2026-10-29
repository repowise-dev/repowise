"""Every file a module page cites exists and is what its section talks about."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation.page_sources import (
    attach_source_links,
    ground_span_citations,
    lint_sources,
    section_symbol_spans,
    source_entry,
    symbol_spans,
)

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


# -- line spans -----------------------------------------------------------------

_SPANS = {"src/shop/billing/charge.py": [(10, 40, "charge_card"), (42, 60, "refund")]}


def test_a_citation_inside_a_known_span_lands_on_the_whole_function():
    out, cited = ground_span_citations("Charges first `billing/charge.py:12-20`.", _SPANS)
    assert "`src/shop/billing/charge.py:10-40`" in out
    assert cited == [
        {
            "path": "src/shop/billing/charge.py",
            "kind": "cited",
            "start_line": 10,
            "end_line": 40,
            "symbol": "charge_card",
        }
    ]


def test_a_citation_matching_no_span_keeps_the_path_and_drops_the_lines():
    out, cited = ground_span_citations("`src/shop/billing/charge.py:200-210`", _SPANS)
    assert out == "`src/shop/billing/charge.py`"
    assert cited == []


def test_a_citation_of_a_file_outside_the_evidence_loses_its_lines():
    out, _ = ground_span_citations("`src/web/views.py:3-9`", _SPANS)
    assert out == "`src/web/views.py`"


def test_a_sources_line_resolves_to_the_spans_its_section_names():
    content = _page("`charge_card()` captures the payment.", "`billing/charge.py`")
    cited = section_symbol_spans(
        content, base="src/shop", members=_MEMBERS, known_paths=_KNOWN, spans_by_file=_SPANS
    )
    assert [(c["symbol"], c["start_line"], c["end_line"]) for c in cited] == [
        ("charge_card", 10, 40)
    ]


def test_symbol_spans_reads_file_context_symbols():
    fc = SimpleNamespace(
        file_path="a.py",
        symbols=[{"name": "f", "start_line": 3, "end_line": 9}, {"name": "g", "start_line": None}],
    )
    assert symbol_spans([fc]) == {"a.py": [(3, 9, "f")]}


def test_source_entry_omits_lines_it_does_not_know():
    assert source_entry("a.py", "dependency") == {"path": "a.py", "kind": "dependency"}


def _git_checkout(root, remote: str | None) -> str:
    sha = "0123456789abcdef0123456789abcdef01234567"
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(sha + "\n")
    if remote:
        (git / "config").write_text(f'[remote "origin"]\n\turl = {remote}\n')
    return sha


def test_source_links_pin_the_commit_and_build_a_forge_url(tmp_path):
    sha = _git_checkout(tmp_path, "https://github.com/acme/app.git")
    page = SimpleNamespace(metadata={"sources": [source_entry("a.py", "step", start_line=3, end_line=9)]})
    assert attach_source_links([page], tmp_path) == 1
    assert page.metadata["sources_commit"] == sha
    assert page.metadata["sources"][0]["url"] == (
        f"https://github.com/acme/app/blob/{sha}/a.py#L3-L9"
    )


def test_without_a_remote_only_the_commit_is_stamped(tmp_path):
    sha = _git_checkout(tmp_path, None)
    page = SimpleNamespace(metadata={"sources": [source_entry("a.py", "dependency")]})
    assert attach_source_links([page], tmp_path) == 0
    assert page.metadata["sources_commit"] == sha
    assert "url" not in page.metadata["sources"][0]


def test_a_dotted_path_keeps_its_leading_dot_and_a_port_is_not_a_citation():
    spans = {".github/ci.py": [(1, 9, "main")]}
    out, cited = ground_span_citations("`.github/ci.py:2-3` on `localhost:8080`", spans)
    assert out == "`.github/ci.py:1-9` on `localhost:8080`"
    assert len(cited) == 1
