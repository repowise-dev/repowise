from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _analyzer(tmp_path: Path, path: str, *, with_repo_root: bool = True) -> DeadCodeAnalyzer:
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def unused_example():\n    return 1\n")
    graph = _build_graph(
        nodes={
            path: {
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "unused_example",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            },
            "app/main.py": {"is_entry_point": True, "symbol_count": 0},
        }
    )
    return DeadCodeAnalyzer(graph, repo_root=tmp_path if with_repo_root else None)


def _write_config(tmp_path: Path, config: str | None) -> None:
    if config is not None:
        target = tmp_path / config
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")


@pytest.mark.parametrize("kind", [DeadCodeKind.UNREACHABLE_FILE, DeadCodeKind.UNUSED_EXPORT])
@pytest.mark.parametrize(
    ("path", "config", "with_repo_root"),
    [
        ("docs/examples/demo.py", None, True),
        ("packages/lib/docs/snippets/demo.py", None, True),
        ("docs_src/tutorial/demo.py", None, False),
        ("examples/demo.py", None, False),
        (
            "documentation-website/Writerside/snippets/demo/src/sample.py",
            "documentation-website/Writerside/writerside.cfg",
            True,
        ),
        ("doc/snippets/demo.py", "doc/mkdocs.yml", True),
        ("web/docs-site/src/demo.py", "web/docs-site/docusaurus.config.ts", True),
    ],
)
def test_docs_examples_are_excluded(
    tmp_path: Path, path: str, config: str | None, with_repo_root: bool, kind: DeadCodeKind
) -> None:
    _write_config(tmp_path, config)
    analyzer = _analyzer(tmp_path, path, with_repo_root=with_repo_root)
    report = analyzer.analyze(
        {
            "detect_unreachable_files": kind == DeadCodeKind.UNREACHABLE_FILE,
            "detect_unused_exports": kind == DeadCodeKind.UNUSED_EXPORT,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    assert not [f for f in report.findings if f.file_path == path and f.kind == kind]


@pytest.mark.parametrize(
    ("path", "config", "with_repo_root"),
    [
        ("app/orphan.py", None, True),
        ("src/pkg/examples/registry.py", None, True),
        ("doc/generator.py", None, True),
        ("documentation-website/service.py", None, True),
        ("docs-api/app/service.py", "docs-api/conf.py", True),
        (
            "documentation-website/Writerside/snippets/demo/src/sample.py",
            "documentation-website/Writerside/writerside.cfg",
            False,
        ),
    ],
)
def test_production_files_still_have_dead_code_findings(
    tmp_path: Path, path: str, config: str | None, with_repo_root: bool
) -> None:
    _write_config(tmp_path, config)
    analyzer = _analyzer(tmp_path, path, with_repo_root=with_repo_root)
    report = analyzer.analyze(
        {
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    assert {f.kind for f in report.findings if f.file_path == path} == {
        DeadCodeKind.UNREACHABLE_FILE,
        DeadCodeKind.UNUSED_EXPORT,
    }
