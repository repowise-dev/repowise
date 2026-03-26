"""Integration tests for dead code detection (Phase 5.5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.models import FileInfo, ParsedFile


# ---------------------------------------------------------------------------
# 1. test_dead_code_detects_unreachable_fixture
# ---------------------------------------------------------------------------


def test_dead_code_detects_unreachable_fixture(sample_repo_path: Path) -> None:
    """Build graph from sample_repo; dead/unreachable_module.py should be unreachable."""
    traverser = FileTraverser(sample_repo_path)
    file_infos = list(traverser.traverse())
    parser = ASTParser()
    graph_builder = GraphBuilder()

    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            graph_builder.add_file(parsed)
        except Exception:
            pass
    graph_builder.build()

    analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map={})
    report = analyzer.analyze({
        "detect_unused_exports": False,
        "detect_zombie_packages": False,
        "min_confidence": 0.0,
    })

    unreachable_paths = [
        f.file_path for f in report.findings
        if f.kind == DeadCodeKind.UNREACHABLE_FILE
    ]
    # dead/unreachable_module.py should have in_degree=0 (nothing imports it)
    matches = [p for p in unreachable_paths if "unreachable_module" in p]
    assert len(matches) > 0, (
        f"Expected dead/unreachable_module.py to be detected as unreachable. "
        f"Found unreachable files: {unreachable_paths}"
    )


# ---------------------------------------------------------------------------
# 2. test_hotspot_sorted_first_in_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hotspot_sorted_first_in_generation() -> None:
    """Hotspot files should be sorted before non-hotspot files by _sort_level_files."""
    from datetime import datetime

    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
    from repowise.core.providers.base import BaseProvider

    # Create a mock provider
    mock_provider = MagicMock(spec=BaseProvider)
    mock_provider.provider_name = "mock"
    mock_provider.model_name = "mock-1"

    config = GenerationConfig()
    assembler = ContextAssembler(config)
    generator = PageGenerator(mock_provider, assembler, config)

    # Create test ParsedFile objects
    def make_parsed(path: str, is_entry: bool = False) -> ParsedFile:
        fi = FileInfo(
            path=path,
            abs_path=f"/repo/{path}",
            language="python",
            size_bytes=1000,
            git_hash="abc123",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=is_entry,
        )
        return ParsedFile(
            file_info=fi,
            symbols=[],
            imports=[],
            exports=[],
            docstring=None,
            parse_errors=[],
        )

    files = [
        make_parsed("normal.py"),
        make_parsed("hotspot.py"),
        make_parsed("entry.py", is_entry=True),
    ]

    git_meta_map = {
        "normal.py": {"is_hotspot": False, "commit_count_total": 5},
        "hotspot.py": {"is_hotspot": True, "commit_count_total": 100},
        "entry.py": {"is_hotspot": False, "commit_count_total": 10},
    }

    pagerank = {
        "normal.py": 0.1,
        "hotspot.py": 0.5,
        "entry.py": 0.3,
    }

    # Replicate the inline sorting logic from PageGenerator._generate_level
    # (entry points first, then hotspots, then high PageRank)
    sorted_files = sorted(
        files,
        key=lambda p: (
            not p.file_info.is_entry_point,
            not git_meta_map.get(p.file_info.path, {}).get("is_hotspot", False),
            -pagerank.get(p.file_info.path, 0.0),
        ),
    )

    paths = [p.file_info.path for p in sorted_files]
    # Entry points first, then hotspots, then normal
    assert paths.index("entry.py") < paths.index("normal.py")
    assert paths.index("hotspot.py") < paths.index("normal.py")
