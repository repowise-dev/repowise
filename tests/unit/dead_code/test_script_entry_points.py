"""Programs and the files a runner names are entry points, not dead files.

From elasticsearch (``distribution/src/bin/elasticsearch``, a shebang
launcher) and repowise itself (``scripts/emit_sample_dsl.py``, run only from
``.github/workflows/ci.yml``; ``docker/``, a folder of Dockerfiles reported as
a zombie package).
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph, _old_date

_OLD = {"commit_count_90d": 0, "last_commit_at": _old_date(400)}


def _unreachable(nodes: dict, source: dict[str, bytes]) -> set[str]:
    graph = _build_graph(nodes)
    report = DeadCodeAnalyzer(
        graph, git_meta_map={path: dict(_OLD) for path in nodes}, source_map=source
    ).analyze({"detect_unused_exports": False, "detect_zombie_packages": False})
    return {f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE}


def test_a_shebang_launcher_is_not_unreachable():
    path = "distribution/src/bin/elasticsearch"
    files = _unreachable(
        {path: {"language": "shell", "symbols": []}, "lib/old.py": {"symbols": []}},
        {path: b"#!/bin/bash\nexec java -cp lib org.Main\n", "lib/old.py": b"X = 1\n"},
    )
    assert files == {"lib/old.py"}


def test_a_python_main_guard_is_not_unreachable():
    body = b"def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    files = _unreachable({"tools/gen.py": {"symbols": []}}, {"tools/gen.py": body})
    assert files == set()


def test_a_script_a_ci_workflow_runs_is_not_unreachable():
    workflow = b"jobs:\n  check:\n    steps:\n      - run: python scripts/emit_sample_dsl.py\n"
    files = _unreachable(
        {"scripts/emit_sample_dsl.py": {"symbols": []}, ".github/workflows/ci.yml": {"language": "yaml"}},
        {"scripts/emit_sample_dsl.py": b"print('dsl')\n", ".github/workflows/ci.yml": workflow},
    )
    assert files == set()


def test_a_file_only_a_doc_names_stays_listed():
    files = _unreachable(
        {"pkg/legacy.py": {"symbols": []}, "docs/notes.md": {"language": "markdown"}},
        {"pkg/legacy.py": b"X = 1\n", "docs/notes.md": b"See pkg/legacy.py.\n"},
    )
    assert files == {"pkg/legacy.py"}


def test_a_folder_of_dockerfiles_is_not_a_zombie_package():
    graph = _build_graph(
        {
            "docker/Dockerfile": {"language": "dockerfile"},
            "docker/entrypoint.sh": {"language": "shell"},
            "pkg/a.py": {"symbols": []},
            "lib/b.py": {"symbols": []},
            # Each folder declares itself a package; only the Dockerfiles differ.
            "docker/pyproject.toml": {"language": "toml"},
            "pkg/pyproject.toml": {"language": "toml"},
            "lib/pyproject.toml": {"language": "toml"},
        }
    )
    report = DeadCodeAnalyzer(graph).analyze({"min_confidence": 0.0})
    zombies = {f.file_path for f in report.findings if f.kind is DeadCodeKind.ZOMBIE_PACKAGE}
    assert "docker" not in zombies
    assert {"pkg", "lib"} <= zombies
