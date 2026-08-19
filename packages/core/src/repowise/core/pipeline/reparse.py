"""Re-parse a repo for ASTs + source bytes without rebuilding the graph.

The scoped-generation and upgrade flows rehydrate the dependency graph from SQL
(no re-resolution, no centrality recompute) but still need live ``ParsedFile``
ASTs and the raw source bytes the generator consumes. This is the one
unavoidable re-work: parsing is parse-cache-backed, so unchanged files skip
tree-sitter.

Kept env-agnostic — the traversal flags are passed in rather than read from
``.repowise/state.json`` here, so the OSS CLI, the OSS server, and hosted can each
resolve those flags their own way and share this parser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def reparse_repo(
    repo_path: Path,
    exclude_patterns: list[str],
    *,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
) -> tuple[list[Any], dict[str, bytes], Any]:
    """Parse files for ASTs + source bytes WITHOUT building/resolving the graph.

    Returns ``(parsed_files, source_map, repo_structure)``. Unreadable or
    unparseable files are skipped, exactly as ``repowise init`` does.

    ``include_submodules`` / ``include_nested_repos`` must match the semantics of
    the original index — a fast index built with ``--include-submodules`` must
    not drop submodule files from the docs re-parse.
    """
    from repowise.core.ingestion import ASTParser, FileTraverser

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()

    parser = ASTParser()
    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
        except Exception:
            pass  # unreadable / unparseable files are skipped, as in init
    return parsed_files, source_map, repo_structure
