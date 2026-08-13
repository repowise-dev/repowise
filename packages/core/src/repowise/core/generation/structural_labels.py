"""Localized fixed text for the deterministic structural wiki pages.

Structural pages are rendered from a Jinja template with no model in the
loop, so the ``language`` setting — which reaches the model-written pages as a
system-prompt instruction — never reached them at all (#1092). This module is
where their fixed copy lives instead.

Two rules make the fallback safe under ``StrictUndefined``:

* :data:`ENGLISH_LABELS` is the complete catalog. Every key a template reads
  is defined here, so a language with no entry of its own renders exactly what
  it renders today rather than a partial mix or a lookup error.
* A localized catalog is *overlaid* on the English one, never substituted for
  it, so a language that translates half the keys still renders the other half
  in English rather than blowing up.

Sentences that vary by more than a heading are stored whole with ``{}``
placeholders and interpolated with ``str.format`` in the template, so a
translation can move the parts around; assembling them from fragments would
pin every language to English word order. ``format`` ignores placeholders a
translation does not use, which is what lets German drop the English
article in ``symbol_overview``.

Code, file paths, symbol names and language names stay untranslated, matching
what the system prompt already tells the model to do.
"""

from __future__ import annotations

from .languages import sanitize_language_code

ENGLISH_LABELS: dict[str, str] = {
    # -- shared ------------------------------------------------------------
    "overview": "Overview",
    "source": "Source",
    "footer": (
        "*Built from the code itself: parsed symbols, the import graph, git history and\n"
        "the knowledge graph. Every statement here is checked against the source rather\n"
        "than written about it.*"
    ),
    "and_more": "and {count} more.",
    "file": "File",
    "file_singular": "file",
    "file_plural": "files",
    "symbol": "Symbol",
    "kind": "Kind",
    "signature": "Signature",
    "questions_heading": "Questions this page answers",
    # -- file page ---------------------------------------------------------
    # The overview sentence is assembled from fragments rather than stored
    # whole: four independent optional clauses would need sixteen variants as
    # one string. A translation that needs a different order pays for it here.
    "is_a": "is a",
    "entry_point": "entry-point",
    "test": "test",
    "source_file": "source file",
    "in_layer": "in the {layer} layer",
    "part_of_module_cluster_intro": "part of the {community} module cluster",
    "exposes_symbols": "It exposes {symbol_count} {symbol_word}.",
    "exposes_symbols_and_depends": (
        "It exposes {symbol_count} {symbol_word} and depends on {file_count} {file_word}."
    ),
    "public_symbol_singular": "public symbol",
    "public_symbol_plural": "public symbols",
    "other_file_singular": "other file",
    "other_file_plural": "other files",
    "public_api": "Public API",
    "depends_on": "Depends on",
    "used_by": "Used by",
    "imported_by_files": "Imported by {count} {file_word} in this repository.",
    "usage_notes": "Usage Notes",
    "part_of_module_cluster": "Part of the **{community}** module cluster.",
    "layer": "Layer",
    "role": "Role",
    "in_the_code": "In the code",
    "question_exports": "What does `{path}` export?",
    "question_where_defined": "Where is `{symbol}` defined?",
    "question_what_imports": "What imports `{path}`?",
    "question_depends_on": "What does `{path}` depend on?",
    # -- symbol spotlight --------------------------------------------------
    "defined_in": "Defined in",
    "async_marker": "async",
    "estimated_complexity": "Estimated complexity",
    "symbol_overview": (
        "`{symbol}` is {article} {kind} defined in `{path}`. It carries no docstring."
    ),
    "article_a": "a",
    "article_an": "an",
    "symbol_kind_fallback": "symbol",
    "decorators": "Decorators",
    "where_used": "Where it is used",
    "importers_summary": (
        "{count} {file_word} {import_verb} the module that defines it. "
        "These are import-level references, not confirmed call sites."
    ),
    "import_verb_singular": "imports",
    "import_verb_plural": "import",
    "implementation": "Implementation",
    "question_what_is": "What is `{symbol}`?",
    "question_which_files_import": ("Which files import the module that defines `{symbol}`?"),
    # -- infrastructure page -----------------------------------------------
    "infrastructure": "Infrastructure",
    "type": "Type",
    "declared_targets": "Declared targets",
    "infra_overview_intro": "`{path}` is an infrastructure file ({language}).",
    "infra_targets_sentence": "It declares {count} {target_word}, listed below.",
    "infra_overview_outro": (
        "Its behaviour is not derivable from structure, so the source is reproduced in full."
    ),
    "target_singular": "named target",
    "target_plural": "named targets",
    # -- API contract page -------------------------------------------------
    "api_contract": "API Contract",
    "language": "Language",
    "operations": "Operations",
    "types": "Types",
    "api_contract_overview": (
        "`{path}` was classified as an API surface. It declares {endpoint_count} "
        "{endpoint_word} and {schema_count} {schema_word}. The list below is taken from "
        "the parsed symbols, so it reflects what the file *declares*; request and "
        "response semantics are not derivable from structure alone."
    ),
    "callable_singular": "callable",
    "callable_plural": "callables",
    "type_singular": "type",
    "type_plural": "types",
    # -- circular-dependency page ------------------------------------------
    "circular_dependency": "Circular Dependency",
    "cycle_overview": (
        "{count} files import each other in a loop, directly or transitively. Nothing in "
        "this group can be loaded, tested or extracted without the rest of it."
    ),
    "cycle_id": "Cycle id",
    "files_in_cycle": "Files in the cycle",
    "and_more_edges": "and {count} more edges.",
    "the_loop": "The loop",
    "where_to_break": "Where to break it",
    "decouple_ranking_description": (
        "Ranked by how many of the cycle's edges each file carries. The file at the top is "
        "the most entangled, so it is usually where an extracted interface or a moved "
        "import buys the most."
    ),
    "imports_in_cycle": "Imports in cycle",
    "imported_by": "Imported by",
    "total": "Total",
    "symbols_defined_in_cycle": "Symbols defined in the cycle",
    "remaining_symbols": ("Symbols for the remaining {count} files are on their own file pages."),
    "total_symbols_in_cycle": "Total symbols in cycle",
}


# code → partial overlay on ENGLISH_LABELS. A code absent here, and any key a
# present code omits, renders English.
LOCALIZED_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "overview": "Überblick",
        "source": "Quelltext",
        "footer": (
            "*Aus dem Code selbst erstellt: geparste Symbole, der Importgraph, die "
            "Git-Historie\nund der Wissensgraph. Jede Aussage hier wird gegen den "
            "Quelltext geprüft, statt\nnur darüber geschrieben zu werden.*"
        ),
        "and_more": "und {count} weitere.",
        "file": "Datei",
        "file_singular": "Datei",
        "file_plural": "Dateien",
        "symbol": "Symbol",
        "kind": "Art",
        "signature": "Signatur",
        "questions_heading": "Fragen, die diese Seite beantwortet",
        "is_a": "ist eine",
        "entry_point": "Einstiegspunkt",
        "test": "Test",
        "source_file": "Quelldatei",
        "in_layer": "in der Schicht {layer}",
        "part_of_module_cluster_intro": "Teil des Modulclusters {community}",
        "exposes_symbols": "Sie stellt {symbol_count} {symbol_word} bereit.",
        "exposes_symbols_and_depends": (
            "Sie stellt {symbol_count} {symbol_word} bereit und hängt von {file_count} "
            "{file_word} ab."
        ),
        "public_symbol_singular": "öffentliches Symbol",
        "public_symbol_plural": "öffentliche Symbole",
        "other_file_singular": "weiteren Datei",
        "other_file_plural": "weiteren Dateien",
        "public_api": "Öffentliche API",
        "depends_on": "Abhängigkeiten",
        "used_by": "Wird verwendet von",
        "imported_by_files": "Importiert von {count} {file_word} in diesem Repository.",
        "usage_notes": "Nutzungshinweise",
        "part_of_module_cluster": "Teil des Modulclusters **{community}**.",
        "layer": "Schicht",
        "role": "Rolle",
        "in_the_code": "Im Code",
        "question_exports": "Was exportiert `{path}`?",
        "question_where_defined": "Wo ist `{symbol}` definiert?",
        "question_what_imports": "Was importiert `{path}`?",
        "question_depends_on": "Wovon hängt `{path}` ab?",
        "defined_in": "Definiert in",
        "async_marker": "asynchron",
        "estimated_complexity": "Geschätzte Komplexität",
        # No {article}: German article agreement does not follow the English
        # vowel rule, and ``str.format`` drops the placeholder we do not use.
        "symbol_overview": (
            "`{symbol}` ist ein {kind}, definiert in `{path}`. Es enthält keinen Docstring."
        ),
        "symbol_kind_fallback": "Symbol",
        "decorators": "Dekoratoren",
        "where_used": "Wo es verwendet wird",
        "importers_summary": (
            "{count} {file_word} {import_verb} das Modul, das es definiert. "
            "Dies sind Referenzen auf Importebene, keine bestätigten Aufrufstellen."
        ),
        "import_verb_singular": "importiert",
        "import_verb_plural": "importieren",
        "implementation": "Implementierung",
        "question_what_is": "Was ist `{symbol}`?",
        "question_which_files_import": (
            "Welche Dateien importieren das Modul, das `{symbol}` definiert?"
        ),
        "infrastructure": "Infrastruktur",
        "type": "Typ",
        "declared_targets": "Deklarierte Ziele",
        "infra_overview_intro": "`{path}` ist eine Infrastrukturdatei ({language}).",
        "infra_targets_sentence": "Sie deklariert {count} {target_word}, unten aufgeführt.",
        "infra_overview_outro": (
            "Ihr Verhalten lässt sich nicht aus der Struktur ableiten, daher wird der "
            "Quelltext vollständig wiedergegeben."
        ),
        "target_singular": "benanntes Ziel",
        "target_plural": "benannte Ziele",
        "api_contract": "API-Vertrag",
        "language": "Sprache",
        "operations": "Operationen",
        "types": "Typen",
        "api_contract_overview": (
            "`{path}` wurde als API-Oberfläche eingestuft. Sie deklariert {endpoint_count} "
            "{endpoint_word} und {schema_count} {schema_word}. Die folgende Liste stammt "
            "aus den geparsten Symbolen und zeigt daher, was die Datei *deklariert*; "
            "Anfrage- und Antwortsemantik lassen sich nicht allein aus der Struktur "
            "ableiten."
        ),
        "callable_singular": "aufrufbare Operation",
        "callable_plural": "aufrufbare Operationen",
        "type_singular": "Typ",
        "type_plural": "Typen",
        "circular_dependency": "Zyklische Abhängigkeit",
        "cycle_overview": (
            "{count} Dateien importieren einander direkt oder transitiv in einer Schleife. "
            "Nichts in dieser Gruppe kann ohne den Rest geladen, getestet oder extrahiert "
            "werden."
        ),
        "cycle_id": "Zyklus-ID",
        "files_in_cycle": "Dateien im Zyklus",
        "and_more_edges": "und {count} weitere Kanten.",
        "the_loop": "Die Schleife",
        "where_to_break": "Wo sie aufgebrochen werden kann",
        "decouple_ranking_description": (
            "Sortiert danach, wie viele Kanten des Zyklus jede Datei trägt. Die oberste "
            "Datei ist am stärksten verflochten; dort bringt eine extrahierte Schnittstelle "
            "oder ein verschobener Import gewöhnlich am meisten."
        ),
        "imports_in_cycle": "Importe im Zyklus",
        "imported_by": "Importiert von",
        "total": "Gesamt",
        "symbols_defined_in_cycle": "Im Zyklus definierte Symbole",
        "remaining_symbols": (
            "Symbole der verbleibenden {count} Dateien stehen auf ihren eigenen Dateiseiten."
        ),
        "total_symbols_in_cycle": "Symbole insgesamt im Zyklus",
    },
}


# page_type → the catalog key naming that page type. The page heading and the
# stored page title come from the same entry, so a wiki cannot show a German
# heading under an English title.
_TITLE_LABEL_KEYS: dict[str, str] = {
    "file_page": "file",
    "symbol_spotlight": "symbol",
    "scc_page": "circular_dependency",
    "api_contract": "api_contract",
    "infra_page": "infrastructure",
}


def resolve_structural_labels(language: str | None) -> dict[str, str]:
    """Return the complete label catalog for *language*.

    English for an absent, malformed or unsupported code, and English for any
    individual key a supported language has not translated.
    """
    labels = ENGLISH_LABELS.copy()
    labels.update(LOCALIZED_LABELS.get(sanitize_language_code(language), {}))
    return labels


def structural_page_title(language: str | None, page_type: str, target: str) -> str:
    """Return the stored page title for a deterministic structural page.

    *target* is a path or a qualified symbol name and is never translated.
    """
    return f"{resolve_structural_labels(language)[_TITLE_LABEL_KEYS[page_type]]}: {target}"
