"""Import extraction: the shared dedup loop over a file's import matches.

What each statement yields is the language's ``LanguageConfig.import_extractor``
(``extractors.imports``); this loop only dedupes and stamps load kinds.
"""

from __future__ import annotations

from ..extractors import node_text
from ..extractors.tcl import is_tcl_switch_arm, unwrap_word
from ..language_configs import LanguageConfig
from ..models import FileInfo, Import, combine_load_kinds
from ..parser_helpers import import_load_kind, python_import_scope


def _extract_imports(
    matches: list[dict],
    config: LanguageConfig,
    file_info: FileInfo,
    src: str,
) -> list[Import]:
    language = file_info.language
    rules = config.import_extractor
    imports: list[Import] = []
    # Statement key -> the Imports it produced, so a repeat folds its load
    # kind into them rather than adding a duplicate.
    seen_imports: dict[str, list[Import]] = {}
    seen_own: set[str] = set()

    for capture_dict in matches:
        stmt_nodes = capture_dict.get("import.statement", [])
        module_nodes = capture_dict.get("import.module", [])

        if not stmt_nodes or not module_nodes:
            continue

        stmt_node = stmt_nodes[0]
        # A Tcl switch arm parses as a command named by its pattern.
        if language == "tcl" and is_tcl_switch_arm(stmt_node):
            continue
        if rules.deduped is not None:
            imports.extend(rules.deduped(stmt_node, module_nodes[0], src, seen_own))
            continue

        raw = node_text(stmt_node, src).strip()
        dedup_key = rules.dedup_key(stmt_node, raw, src) if rules.dedup_key else raw
        kind = import_load_kind(stmt_node, language, src)
        scopes = python_import_scope(stmt_node) if language == "python" else None
        if dedup_key in seen_imports:
            # A lazy copy must not hide a load-time twin: runtime wins.
            for imp in seen_imports[dedup_key]:
                imp.type_only, imp.deferred = combine_load_kinds(
                    (imp.type_only, imp.deferred), kind
                )
                # The same statement in another function binds there too; a
                # module-level copy binds it file-wide.
                imp.scopes = (
                    imp.scopes + scopes if imp.scopes is not None and scopes is not None else None
                )
            continue
        seen_imports[dedup_key] = []

        module_text = node_text(module_nodes[0], src).strip().strip("\"'` ")
        if language == "tcl" and module_nodes[0].type == "braced_word":
            module_text = unwrap_word(module_text)
        if not module_text:
            continue

        found = rules.statement(stmt_node, module_nodes[0], module_text, raw, language, src)
        for imp in found:
            imp.type_only, imp.deferred = kind
            imp.scopes = scopes
        seen_imports[dedup_key] = found
        imports.extend(found)

    if rules.finalize is not None:
        imports = rules.finalize(imports)
    return imports
