"""Import extraction: the shared dedup loop over a file's import matches.

What each statement yields is the language's ``LanguageConfig.import_extractor``
(``extractors.imports``); this loop only dedupes and stamps load kinds.
"""

from __future__ import annotations

from tree_sitter import Node

from ..extractors import node_text
from ..extractors.tcl import is_tcl_switch_arm, unwrap_word
from ..language_configs import LanguageConfig
from ..models import FileInfo, Import, combine_load_kinds
from ..parser_helpers import (
    import_load_kind,
    python_bound_module_name,
    python_import_scope,
    python_load_time_names,
)


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
    # Python: names module-level code mentions at load, read once per file.
    load_names: list[frozenset[str]] = []

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
                imp.type_only, imp.deferred, imp.guarded = combine_load_kinds(
                    (imp.type_only, imp.deferred, imp.guarded), kind
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
        module_form = language == "python" and _is_module_form(
            stmt_node, module_nodes[0], src, load_names
        )
        for imp in found:
            imp.type_only, imp.deferred, imp.guarded = kind
            imp.scopes = scopes
            imp.module_form = module_form
        seen_imports[dedup_key] = found
        imports.extend(found)

    if rules.finalize is not None:
        imports = rules.finalize(imports)
    return imports


def _is_module_form(
    stmt_node: Node, module_node: Node, src: str, load_names: list[frozenset[str]]
) -> bool:
    """``import b`` whose bound name no module-level code mentions at load.

    *load_names* caches the file's load-time names after the first call.
    """
    if stmt_node.type != "import_statement":
        return False
    if not load_names:
        root = stmt_node
        while root.parent is not None:
            root = root.parent
        load_names.append(python_load_time_names(root, src))
    return python_bound_module_name(module_node, src) not in load_names[0]
