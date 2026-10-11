"""The label catalog behind localized deterministic structural pages (#1092).

The catalog's contract is narrow and the templates render under
``StrictUndefined``, so the interesting cases are the ones where a lookup
could come back missing rather than merely untranslated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.core.generation.structural_labels import (
    ENGLISH_LABELS,
    LOCALIZED_LABELS,
    resolve_structural_labels,
    structural_page_title,
)

TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "src"
    / "repowise"
    / "core"
    / "generation"
    / "templates"
)

STRUCTURAL_TEMPLATES = [
    "_footer.j2",
    "api_contract.j2",
    "file_page.j2",
    "infra_page.j2",
    "scc_page.j2",
    "symbol_spotlight.j2",
]


def test_german_labels_are_used_where_they_exist() -> None:
    assert resolve_structural_labels("de")["overview"] == "Überblick"


@pytest.mark.parametrize("code", [None, "", "xx", "klingon", "  ", "de-DE"])
def test_an_absent_or_unsupported_code_renders_the_full_english_catalog(code: str | None) -> None:
    # Never a partial mix and never a missing key: a template reading any label
    # under StrictUndefined has to get a string back.
    assert resolve_structural_labels(code) == ENGLISH_LABELS


@pytest.mark.parametrize("code", ["DE", " de ", "de\n"])
def test_a_code_is_sanitized_before_lookup(code: str) -> None:
    # The same scrubbing the system prompt applies, so a config value that
    # localizes the model-written pages localizes these too.
    assert resolve_structural_labels(code)["overview"] == "Überblick"


def test_a_partial_translation_falls_back_per_key_not_wholesale() -> None:
    labels = resolve_structural_labels("de")
    assert labels.keys() == ENGLISH_LABELS.keys()
    missing = ENGLISH_LABELS.keys() - LOCALIZED_LABELS["de"].keys()
    for key in missing:
        assert labels[key] == ENGLISH_LABELS[key]


def test_a_translation_only_defines_keys_the_english_catalog_has() -> None:
    # A typo'd key in a translation would otherwise be silently dead.
    for code, catalog in LOCALIZED_LABELS.items():
        assert catalog.keys() <= ENGLISH_LABELS.keys(), code


def test_no_label_key_shadows_a_dict_attribute() -> None:
    # Templates read labels with dot access, which resolves an attribute
    # before an item: a key named ``items`` or ``copy`` would render a bound
    # method rather than the text.
    assert not [key for key in ENGLISH_LABELS if hasattr({}, key)]


def test_every_key_the_templates_read_is_in_the_catalog() -> None:
    read = set()
    for name in STRUCTURAL_TEMPLATES:
        source = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        read.update(re.findall(r"\blabels\.([a-z_]+)", source))
    assert read <= ENGLISH_LABELS.keys(), read - ENGLISH_LABELS.keys()


def test_no_catalog_key_is_unread_by_any_template() -> None:
    read = set()
    for name in STRUCTURAL_TEMPLATES:
        source = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        read.update(re.findall(r"\blabels\.([a-z_]+)", source))
    # Titles are built in Python rather than in a template.
    read.update({"file", "symbol", "circular_dependency", "api_contract", "infrastructure"})
    assert ENGLISH_LABELS.keys() <= read, ENGLISH_LABELS.keys() - read


def test_a_translation_keeps_every_placeholder_the_english_string_uses() -> None:
    # ``str.format`` raises KeyError on a placeholder the caller does not
    # supply, so a translation may drop one but never invent one.
    for code, catalog in LOCALIZED_LABELS.items():
        for key, text in catalog.items():
            english = set(re.findall(r"\{(\w+)\}", ENGLISH_LABELS[key]))
            translated = set(re.findall(r"\{(\w+)\}", text))
            assert translated <= english, (code, key, translated - english)


def test_page_titles_come_from_the_same_catalog_as_the_headings() -> None:
    assert structural_page_title("de", "file_page", "src/service.py") == "Datei: src/service.py"
    assert structural_page_title("de", "api_contract", "src/api.py") == "API-Vertrag: src/api.py"
    assert structural_page_title("de", "infra_page", "Dockerfile") == "Infrastruktur: Dockerfile"


def test_page_titles_fall_back_to_english_and_never_translate_the_target() -> None:
    assert structural_page_title("xx", "symbol_spotlight", "service.parse_file") == (
        "Symbol: service.parse_file"
    )
    assert structural_page_title("de", "symbol_spotlight", "service.parse_file") == (
        "Symbol: service.parse_file"
    )
