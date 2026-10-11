"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo


def _make_file_info(path: str, language: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
