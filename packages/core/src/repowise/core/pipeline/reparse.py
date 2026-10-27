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
    from repowise.core.ingestion import FileTraverser
    from repowise.core.pipeline.phases.ingestion import (
        _collect_parsed,
        _read_sources,
        parse_guarded,
        report_parse_failures,
    )

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()

    # Same deadline pool as init, so a file that never finishes parsing is
    # skipped here too instead of hanging the re-parse.
    fi_and_bytes = _read_sources(file_infos, None)
    to_parse = [(idx, item, "") for idx, item in enumerate(fi_and_bytes)]
    results = parse_guarded(to_parse)
    report_parse_failures(to_parse, results)  # logs each file it gave up on
    # Unreadable / unparseable files are skipped, as in init.
    parsed_files, source_map = _collect_parsed(fi_and_bytes, dict(enumerate(results)))
    return parsed_files, source_map, repo_structure
