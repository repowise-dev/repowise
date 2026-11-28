"""A warm framework scan cache must give the same graph as a cold build.

The tree below exercises every cached scan: tests naming helpers by path
(``Path(__file__)`` anchors and relative literals), fixture requests only the
text shows (``getfixturevalue``, ``pytestmark``), and code running Alembic. It
is built once to warm ``.repowise/framework_scan_cache.pkl``, edited, and built
again; the result is compared with a from-scratch build of the edited tree.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from repowise.core.ingestion.framework_edges import alembic_runner, pytest_edges, test_path_strings
from repowise.core.pipeline.incremental import build_repo_graph

_FILES = {
    "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
    "app/__init__.py": "",
    "app/db.py": "import alembic\n\ndef migrate():\n    alembic.command.upgrade(None, 'head')\n",
    "app/core.py": "def run():\n    return 1\n",
    "migrations/env.py": "print('env')\n",
    "migrations/versions/001_init.py": "revision = '001'\n",
    "tests/__init__.py": "",
    "tests/conftest.py": (
        "import pytest\n\n@pytest.fixture\ndef client():\n    return 1\n\n"
        "@pytest.fixture\ndef db(client):\n    return client\n"
    ),
    "tests/_server.py": "print('serving')\n",
    "tests/test_server.py": (
        "import subprocess, sys\nfrom pathlib import Path\n\n"
        "HERE = Path(__file__).parent\n\n"
        "def test_runs(client):\n"
        "    subprocess.run([sys.executable, HERE / '_server.py'])\n"
        "    subprocess.run([sys.executable, 'later_helper.py'])\n"
    ),
    "tests/test_lookup.py": (
        "import pytest\n\npytestmark = pytest.mark.usefixtures('client')\n\n"
        "def test_lookup(request):\n    request.getfixturevalue('db')\n"
    ),
    "tests/api/conftest.py": "import pytest\n\n@pytest.fixture\ndef token():\n    return 't'\n",
    "tests/api/test_api.py": "def test_api(token, db):\n    assert token\n",
    "tests/test_migrate.py": "from app.db import migrate\n\ndef test_migrate():\n    migrate()\n",
}
# One test file changes; a helper an unchanged test already names appears.
_EDITS = {
    "tests/api/test_api.py": (
        "import pytest\n\ndef test_api(token, db, request):\n"
        "    request.getfixturevalue('client')\n"
    ),
    "tests/later_helper.py": "print('later')\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _edges(graph_builder: Any) -> set[tuple]:
    return {
        (u, v, d.get("edge_type"), d.get("hint_source"), tuple(d.get("imported_names") or ()))
        for u, v, d in graph_builder.graph().edges(data=True)
    }


def _framework(edges: set[tuple]) -> set[tuple]:
    return {e for e in edges if e[2] in ("framework", "framework_binds")}


def _build(root: Path) -> Any:
    return build_repo_graph(root, [], collect_sources=True)[2]


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Which files each cached scan actually ran on."""
    calls: dict[str, list[str]] = {"path_strings": [], "lex": [], "alembic": []}

    def wrap(module: Any, name: str, key: str, path_of: Any) -> None:
        real = getattr(module, name)

        def spy(*args: Any) -> Any:
            calls[key].append(path_of(args))
            return real(*args)

        monkeypatch.setattr(module, name, spy)

    wrap(test_path_strings, "path_string_candidates", "path_strings", lambda a: a[0])
    wrap(pytest_edges, "_lex", "lex", lambda a: a[0][:40])
    wrap(alembic_runner, "names_alembic", "alembic", lambda a: a[0])
    return calls


def test_warm_scan_cache_matches_a_cold_build(
    tmp_path: Path, counted: dict[str, list[str]]
) -> None:
    tree = tmp_path / "repo"
    _write(tree, _FILES)
    _build(tree)
    assert (tree / ".repowise" / "framework_scan_cache.pkl").is_file()
    cold_counts = {k: len(v) for k, v in counted.items()}
    assert all(cold_counts.values()), cold_counts

    _write(tree, _EDITS)
    for calls in counted.values():
        calls.clear()
    warm = _edges(_build(tree))

    cold_tree = tmp_path / "cold"
    shutil.copytree(tree, cold_tree, ignore=shutil.ignore_patterns(".repowise"))
    cold = _edges(_build(cold_tree))

    assert warm == cold
    # The edited test and the new helper's own edges are in the comparison.
    framework = _framework(cold)
    assert ("tests/test_server.py", "tests/later_helper.py") in {e[:2] for e in framework}
    assert ("tests/api/test_api.py", "tests/api/conftest.py") in {e[:2] for e in framework}
    assert ("app/db.py", "migrations/versions/001_init.py") in {e[:2] for e in framework}


def test_warm_scan_reruns_only_changed_files(
    tmp_path: Path, counted: dict[str, list[str]]
) -> None:
    tree = tmp_path / "repo"
    _write(tree, _FILES)
    _build(tree)
    _write(tree, _EDITS)
    for calls in counted.values():
        calls.clear()

    _build(tree)

    # The edited test, and the new helper (a test-tree file) seen for the first time.
    assert counted["path_strings"] == ["tests/api/test_api.py", "tests/later_helper.py"]
    assert len(counted["lex"]) == 1  # the edited test's tokenize pass only
    assert counted["alembic"] == ["tests/api/test_api.py", "tests/later_helper.py"]


def test_scan_cache_from_another_scanner_build_is_ignored(
    tmp_path: Path, counted: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.ingestion.framework_edges import scan_cache

    tree = tmp_path / "repo"
    _write(tree, _FILES)
    _build(tree)
    cold = sorted(counted["path_strings"])
    counted["path_strings"].clear()
    monkeypatch.setattr(scan_cache, "scan_fingerprint", lambda: "another-build")

    _build(tree)

    assert sorted(counted["path_strings"]) == cold
