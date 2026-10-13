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
    "footer": "*Generated from parsed code, the import graph and git history.*",
    "and_more": "and {count} more.",
    "file": "File",
    "file_singular": "file",
    "file_plural": "files",
    "symbol": "Symbol",
    "kind": "Kind",
    "questions_heading": "Questions this page answers",
    # -- file page ---------------------------------------------------------
    # The opening sentences carry a ``{subject}`` slot the template fills with
    # the file's name for the page's first sentence and ``it_subject`` after.
    "and_word": "and",
    "and_count_more": "{count} more",
    "it_subject": "It",
    "file_defines": "{subject} defines {names}.",
    "file_imported_by": "{subject} is imported by {names}.",
    "file_imported_by_count": "{subject} is imported by {count} files{tests}.",
    "of_them_tests": " ({count} of them tests)",
    "largest_share": "{count} of them are in {directory}.",
    "largest_share_code": "Of the others, {count} are in {directory}.",
    "all_in_directory": "All of them are in {directory}.",
    "file_imports": "{subject} imports {names}.",
    "file_imports_count": "{subject} imports {count} files from this repository.",
    "file_layer": "{subject} belongs to the {layer} layer.",
    "file_layer_boundary": "{subject} belongs to the {layer} layer, and other layers import it.",
    "file_layer_entry": "{subject} belongs to the {layer} layer and is an entry point into it.",
    "file_entry_point": "{subject} is an entry point.",
    "file_fallback": "{subject} is {article} {language} file.",
    "public_api": "Public API",
    "depends_on": "Depends on",
    "used_by": "Used by",
    # Symbols the API list leaves out, named so the identifier stays on the
    # page even though the entry does not.
    "also_defined": "Also defined: {names}.",
    # -- file page: history ------------------------------------------------
    "history": "History",
    "history_commits": "{total} {commit_word} in its history, {recent} in the last 90 days.",
    "history_last_commit": "The last landed on {date}.",
    # ``history_owner`` when only commit shares are known (no blame);
    # ``history_owner_lines`` when blame chose the owner, naming both shares
    # because the top blame author need not be the top committer.
    "history_owner": "**{owner}** is its primary maintainer, at {pct}% of commits.",
    "history_owner_lines": "**{owner}** wrote {line}% of its current lines ({commit}% of commits).",
    "history_owner_lines_only": "**{owner}** wrote {line}% of its current lines.",
    "history_fixes": "{count} of those commits fixed a bug.",
    "history_hotspot": "It is one of the repository's change hotspots.",
    "history_stable": "It has been stable: nothing has changed it lately.",
    "commit_singular": "commit",
    "commit_plural": "commits",
    "changes_with": "Changes together with",
    "changes_with_intro": (
        "Files that change in the same commits as this one without importing it "
        "or being imported by it."
    ),
    "changes_with_entry": "{count} shared {commit_word}",
    "last_together": "last together on {date}",
    "decisions_heading": "Decisions touching this file",
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
    "call_sites_summary": "Reached by {count} resolved {call_word}.",
    "imported_by_heading": "Files importing this module",
    "call_site_singular": "call",
    "call_site_plural": "calls",
    "in_file": "in `{path}`",
    "question_what_calls": "What calls `{symbol}`?",
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
        "footer": "*Erstellt aus geparstem Code, dem Importgraphen und der Git-Historie.*",
        "and_more": "und {count} weitere.",
        "file": "Datei",
        "file_singular": "Datei",
        "file_plural": "Dateien",
        "symbol": "Symbol",
        "kind": "Art",
        "questions_heading": "Fragen, die diese Seite beantwortet",
        "and_word": "und",
        "and_count_more": "{count} weitere",
        "it_subject": "Die Datei",
        "file_defines": "{subject} definiert {names}.",
        "file_imported_by": "{subject} wird von {names} importiert.",
        "file_imported_by_count": "{subject} wird von {count} Dateien{tests} importiert.",
        "of_them_tests": " (davon {count} Tests)",
        "largest_share": "{count} davon liegen in {directory}.",
        "largest_share_code": "Von den übrigen liegen {count} in {directory}.",
        "all_in_directory": "Alle liegen in {directory}.",
        "file_imports": "{subject} importiert {names}.",
        "file_imports_count": "{subject} importiert {count} Dateien aus diesem Repository.",
        "file_layer": "{subject} gehört zur Schicht {layer}.",
        "file_layer_boundary": (
            "{subject} gehört zur Schicht {layer} und wird aus anderen Schichten importiert."
        ),
        "file_layer_entry": (
            "{subject} gehört zur Schicht {layer} und ist ein Einstiegspunkt in sie."
        ),
        "file_entry_point": "{subject} ist ein Einstiegspunkt.",
        "file_fallback": "{subject} ist eine {language}-Datei.",
        "public_api": "Öffentliche API",
        "depends_on": "Abhängigkeiten",
        "used_by": "Wird verwendet von",
        "also_defined": "Ebenfalls definiert: {names}.",
        "history": "Historie",
        "history_commits": (
            "{total} {commit_word} in ihrer Historie, {recent} in den letzten 90 Tagen."
        ),
        "history_last_commit": "Der letzte stammt vom {date}.",
        "history_owner": "**{owner}** betreut sie hauptsächlich, mit {pct}% der Commits.",
        "history_owner_lines": (
            "**{owner}** hat {line}% ihrer aktuellen Zeilen geschrieben ({commit}% der Commits)."
        ),
        "history_owner_lines_only": "**{owner}** hat {line}% ihrer aktuellen Zeilen geschrieben.",
        "history_fixes": "{count} dieser Commits haben einen Fehler behoben.",
        "history_hotspot": "Sie gehört zu den Änderungs-Hotspots des Repositorys.",
        "history_stable": "Sie ist stabil: zuletzt hat sich nichts an ihr geändert.",
        "commit_singular": "Commit",
        "commit_plural": "Commits",
        "changes_with": "Ändert sich gemeinsam mit",
        "changes_with_intro": (
            "Dateien, die in denselben Commits geändert werden wie diese, ohne sie zu "
            "importieren oder von ihr importiert zu werden."
        ),
        "changes_with_entry": "{count} gemeinsame {commit_word}",
        "last_together": "zuletzt gemeinsam am {date}",
        "decisions_heading": "Entscheidungen zu dieser Datei",
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
        "call_sites_summary": "Erreicht von {count} aufgelösten {call_word}.",
        "imported_by_heading": "Dateien, die dieses Modul importieren",
        "call_site_singular": "Aufrufstelle",
        "call_site_plural": "Aufrufstellen",
        "in_file": "in `{path}`",
        "question_what_calls": "Was ruft `{symbol}` auf?",
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


def is_structural_title(language: str | None, page_type: str, target: str, title: str | None) -> bool:
    """True when *title* is exactly what :func:`structural_page_title` would store.

    A structural title only restates the page's own target in a localized
    wrapper, so a caller that still has the target can rebuild it and need not
    ship it. False for a page type with no structural title and for a title that
    differs in any way (an edited or prose title carries information).
    """
    if page_type not in _TITLE_LABEL_KEYS or not target or not title:
        return False
    return title == structural_page_title(language, page_type, target)
