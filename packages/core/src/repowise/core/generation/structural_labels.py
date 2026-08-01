"""Localized fixed text for deterministic structural wiki pages.

The English catalog is deliberately complete so unsupported languages retain
the current rendered copy instead of leaving a Jinja lookup undefined.
"""

ENGLISH_LABELS: dict[str, str] = {
    "overview": "Overview",
    "is_a": "is a",
    "entry_point": "entry-point",
    "test": "test",
    "source_file": "source file",
    "in_layer": "in the {layer} layer",
    "part_of_module_cluster_intro": "part of the {community} module cluster",
    "exposes": "It exposes",
    "public_symbol": "public symbol",
    "public_symbols": "public symbols",
    "depends_on_other_files": "and depends on",
    "other_file": "other file",
    "other_files": "other files",
    "public_api": "Public API",
    "symbol": "Symbol",
    "kind": "Kind",
    "signature": "Signature",
    "no_public_symbols": "No public symbols were extracted from this file.",
    "depends_on": "Depends on",
    "and_more": "and {count} more.",
    "no_dependencies": "No internal dependencies resolved.",
    "used_by": "Used by",
    "imported_by_files": "Imported by {count} {file_word} in this repository.",
    "no_callers": "No internal callers were resolved for this file.",
    "usage_notes": "Usage Notes",
    "part_of_module_cluster": "Part of the **{community}** module cluster.",
    "layer": "Layer",
    "role": "Role",
    "defined_in": "Defined in",
    "estimated_complexity": "Estimated complexity",
    "async": "async",
    "no_docstring": "It carries no docstring.",
    "decorators": "Decorators",
    "where_used": "Where it is used",
    "importers_summary": (
        "{count} {file_word} {import_verb} the module that defines it. "
        "These are import-level references, not confirmed call sites."
    ),
    "no_importers": "No importers of the defining module were resolved.",
    "implementation": "Implementation",
    "files": "Files",
    "entry_points": "Entry points",
    "edge_connectors": "Edge connectors",
    "file_singular": "file",
    "file_plural": "files",
    "layer_singular": "layer",
    "layer_plural": "layers",
    "import_verb_singular": "imports",
    "import_verb_plural": "import",
    "edge_singular": "edge",
    "edge_plural": "edges",
    "layer_holds_files": "This layer holds {count} {file_word}.",
    "layer_mid_stack": (
        "It is called by {in_count} {in_layer_word} and calls into {out_count}, "
        "so it sits mid-stack: work flows in from above and out to the layers below."
    ),
    "layer_top_stack": (
        "It calls into {count} {layer_word} and nothing in this repository imports it, "
        "which puts it at the top of the stack."
    ),
    "layer_foundational": (
        "{count} {layer_word} {import_verb} it and it imports none itself, "
        "which makes it foundational."
    ),
    "layer_standalone": "It has no resolved inter-layer edges, so it stands on its own.",
    "architecture": "Architecture",
    "key_files": "Key files",
    "edge_connectors_description": "Files where this layer meets the outside world.",
    "neighbouring_layers": "Neighbouring layers",
    "imports_this_layer": "**{layer}** imports this layer ({count} {edge_word})",
    "this_layer_imports": "This layer imports **{layer}** ({count} {edge_word})",
    "on_guided_tour": "On the guided tour",
    "step": "Step {number}",
    "infrastructure": "Infrastructure",
    "type": "Type",
    "declared_targets": "Declared targets",
    "infrastructure_overview": (
        "`{file_path}` is an infrastructure file ({language}). {targets_sentence} "
        "Its behaviour is not derivable from structure, so the source is reproduced in full."
    ),
    "target_singular": "named target",
    "target_plural": "named targets",
    "callable_singular": "callable operation",
    "callable_plural": "callable operations",
    "type_singular": "type",
    "type_plural": "types",
    "infrastructure_targets_sentence": "It declares {count} {target_word}, listed below.",
    "source": "Source",
    "footer": (
        "*Built from the code itself: parsed symbols, the import graph, git history and\n"
        "the knowledge graph. Every statement here is checked against the source rather\n"
        "than written about it.*"
    ),
    "circular_dependency": "Circular Dependency",
    "cycle_overview": (
        "{count} files import each other in a loop, directly or transitively. Nothing in this "
        "group can be loaded, tested or extracted without the rest of it."
    ),
    "files_in_cycle": "Files in the cycle",
    "and_more_edges": "and {count} more edges.",
    "the_loop": "The loop",
    "where_to_break": "Where to break it",
    "decouple_ranking_description": (
        "Ranked by how many of the cycle's edges each file carries. The file at the top is the "
        "most entangled, so it is usually where an extracted interface or a moved import buys the most."
    ),
    "file": "File",
    "imports_in_cycle": "Imports in cycle",
    "imported_by": "Imported by",
    "total": "Total",
    "symbols_defined_in_cycle": "Symbols defined in the cycle",
    "remaining_symbols": "Symbols for the remaining {count} files are on their own file pages.",
    "total_symbols_in_cycle": "Total symbols in cycle",
    "api_contract": "API Contract",
    "language": "Language",
    "operations": "Operations",
    "types": "Types",
    "api_contract_overview": (
        "`{file_path}` was classified as an API surface. It declares {endpoint_count} "
        "{endpoint_word} and {schema_count} {schema_word}. The list below is "
        "taken from the parsed symbols, so it reflects what the file *declares*; request and "
        "response semantics are not derivable from structure alone."
    ),
}


GERMAN_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "overview": "Überblick",
        "is_a": "ist eine",
        "entry_point": "Einstiegspunkt",
        "test": "Test",
        "source_file": "Quelldatei",
        "in_layer": "in der Schicht {layer}",
        "part_of_module_cluster_intro": "Teil des Modulclusters {community}",
        "exposes": "Sie stellt",
        "public_symbol": "öffentliches Symbol",
        "public_symbols": "öffentliche Symbole",
        "depends_on_other_files": "und hängt von",
        "other_file": "weiterer Datei",
        "other_files": "weiteren Dateien",
        "public_api": "Öffentliche API",
        "symbol": "Symbol",
        "kind": "Art",
        "signature": "Signatur",
        "no_public_symbols": "Aus dieser Datei wurden keine öffentlichen Symbole extrahiert.",
        "depends_on": "Abhängigkeiten",
        "and_more": "und {count} weitere.",
        "no_dependencies": "Keine internen Abhängigkeiten aufgelöst.",
        "used_by": "Wird verwendet von",
        "imported_by_files": "Importiert von {count} {file_word} in diesem Repository.",
        "no_callers": "Für diese Datei wurden keine internen Aufrufer aufgelöst.",
        "usage_notes": "Nutzungshinweise",
        "part_of_module_cluster": "Teil des Modulclusters **{community}**.",
        "layer": "Schicht",
        "role": "Rolle",
        "defined_in": "Definiert in",
        "estimated_complexity": "Geschätzte Komplexität",
        "async": "asynchron",
        "no_docstring": "Es enthält keinen Docstring.",
        "decorators": "Dekoratoren",
        "where_used": "Wo es verwendet wird",
        "importers_summary": (
            "{count} {file_word} {import_verb} das Modul, das es definiert. "
            "Dies sind Referenzen auf Importebene, keine bestätigten Aufrufstellen."
        ),
        "no_importers": "Keine Importeure des definierenden Moduls wurden aufgelöst.",
        "implementation": "Implementierung",
        "files": "Dateien",
        "entry_points": "Einstiegspunkte",
        "edge_connectors": "Randverbindungen",
        "file_singular": "Datei",
        "file_plural": "Dateien",
        "layer_singular": "Schicht",
        "layer_plural": "Schichten",
        "import_verb_singular": "importiert",
        "import_verb_plural": "importieren",
        "edge_singular": "Kante",
        "edge_plural": "Kanten",
        "layer_holds_files": "Diese Schicht enthält {count} {file_word}.",
        "layer_mid_stack": (
            "Sie wird von {in_count} {in_layer_word} aufgerufen und ruft {out_count} auf; "
            "damit liegt sie in der Mitte des Stacks: Arbeit fließt von oben ein und zu den "
            "darunterliegenden Schichten weiter."
        ),
        "layer_top_stack": (
            "Sie ruft {count} {layer_word} auf und wird von nichts in diesem Repository "
            "importiert; damit liegt sie oben im Stack."
        ),
        "layer_foundational": (
            "{count} {layer_word} {import_verb} sie, und sie importiert selbst nichts; "
            "dadurch ist sie grundlegend."
        ),
        "layer_standalone": "Sie hat keine aufgelösten schichtübergreifenden Kanten und steht für sich.",
        "architecture": "Architektur",
        "key_files": "Wichtige Dateien",
        "edge_connectors_description": "Dateien, an denen diese Schicht auf die Außenwelt trifft.",
        "neighbouring_layers": "Benachbarte Schichten",
        "imports_this_layer": "**{layer}** importiert diese Schicht ({count} {edge_word})",
        "this_layer_imports": "Diese Schicht importiert **{layer}** ({count} {edge_word})",
        "on_guided_tour": "In der geführten Tour",
        "step": "Schritt {number}",
        "infrastructure": "Infrastruktur",
        "type": "Typ",
        "declared_targets": "Deklarierte Ziele",
        "infrastructure_overview": (
            "`{file_path}` ist eine Infrastrukturdatei ({language}). {targets_sentence} "
            "Ihr Verhalten kann nicht aus der Struktur abgeleitet werden; daher wird der "
            "Quelltext vollständig wiedergegeben."
        ),
        "target_singular": "benanntes Ziel",
        "target_plural": "benannte Ziele",
        "callable_singular": "aufrufbare Operation",
        "callable_plural": "aufrufbare Operationen",
        "type_singular": "Typ",
        "type_plural": "Typen",
        "infrastructure_targets_sentence": "Sie deklariert {count} {target_word}, unten aufgeführt.",
        "source": "Quelltext",
        "footer": (
            "*Aus dem Code selbst erstellt: geparste Symbole, der Importgraph, die Git-Historie\n"
            "und der Wissensgraph. Jede Aussage hier wird gegen den Quelltext geprüft, statt\n"
            "nur darüber geschrieben zu werden.*"
        ),
        "circular_dependency": "Zyklische Abhängigkeit",
        "cycle_overview": (
            "{count} Dateien importieren einander direkt oder transitiv in einer Schleife. "
            "Nichts in dieser Gruppe kann ohne den Rest geladen, getestet oder extrahiert werden."
        ),
        "files_in_cycle": "Dateien im Zyklus",
        "and_more_edges": "und {count} weitere Kanten.",
        "the_loop": "Die Schleife",
        "where_to_break": "Wo sie aufgebrochen werden kann",
        "decouple_ranking_description": (
            "Sortiert danach, wie viele Kanten des Zyklus jede Datei trägt. Die oberste Datei ist "
            "am stärksten verflochten; dort bringt eine extrahierte Schnittstelle oder ein "
            "verschobener Import gewöhnlich am meisten."
        ),
        "file": "Datei",
        "imports_in_cycle": "Importe im Zyklus",
        "imported_by": "Importiert von",
        "total": "Gesamt",
        "symbols_defined_in_cycle": "Im Zyklus definierte Symbole",
        "remaining_symbols": "Symbole der verbleibenden {count} Dateien stehen auf ihren eigenen Dateiseiten.",
        "total_symbols_in_cycle": "Symbole insgesamt im Zyklus",
        "api_contract": "API-Vertrag",
        "language": "Sprache",
        "operations": "Operationen",
        "types": "Typen",
        "api_contract_overview": (
            "`{file_path}` wurde als API-Oberfläche klassifiziert. Es deklariert {endpoint_count} "
            "{endpoint_word} und {schema_count} {schema_word}. "
            "Die folgende Liste stammt aus den geparsten Symbolen und zeigt daher, was die Datei "
            "*deklariert*; Anfrage- und Antwortsemantik lassen sich nicht allein aus der Struktur ableiten."
        ),
    }
}


def resolve_structural_labels(language: str | None) -> dict[str, str]:
    """Return a complete label catalog, localized when supported."""
    labels = ENGLISH_LABELS.copy()
    labels.update(GERMAN_LABELS.get(language or "", {}))
    return labels


_STRUCTURAL_TITLE_LABELS: dict[str, str] = {
    "file_page": "file",
    "symbol_spotlight": "symbol",
    "scc_page": "circular_dependency",
    "layer_page": "layer",
    "api_contract": "api_contract",
    "infra_page": "infrastructure",
}


def structural_page_title(language: str | None, page_type: str, target: str) -> str:
    """Build a localized title for a deterministic structural page."""
    return f"{resolve_structural_labels(language)[_STRUCTURAL_TITLE_LABELS[page_type]]}: {target}"
