"""Equivalence tests for HintRegistry.extract_all fed from a traversed file list.

The ingestion pipeline passes the traverser's ``FileInfo.path`` list into
``extract_all(file_paths=...)`` so the extractor fleet queries the indexed
file set directly instead of re-walking the tree. Two guarantees are pinned
here:

1. When the fed list equals what the registry's own walk would find, the
   emitted edges are identical (pure mechanism swap).
2. Files excluded from the list (gitignored build output, generated
   ``*.Designer.cs``, ``local_settings.py``) produce no hint edges, because
   the index does not contain them and an edge to a nonexistent node is a
   dangling reference downstream.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.fs_walk import PRUNED_DIRS_DERIVED, WalkSnapshot
from repowise.core.ingestion.dynamic_hints import HintRegistry


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A mixed-language fixture exercising django, node, pytest, and dotnet."""
    _write(tmp_path, "myapp/__init__.py", "")
    _write(tmp_path, "myapp/urls.py", "urlpatterns = []\n")
    _write(tmp_path, "settings.py", 'INSTALLED_APPS = ["myapp"]\nROOT_URLCONF = "myapp.urls"\n')
    _write(tmp_path, "urls.py", "from django.urls import include\ninclude('myapp.urls')\n")
    _write(tmp_path, "config/settings/base.py", 'INSTALLED_APPS = ["myapp"]\n')
    _write(tmp_path, "package.json", '{"name": "x", "main": "./index.js"}\n')
    _write(tmp_path, "index.js", "module.exports = {}\n")
    _write(
        tmp_path,
        "conftest.py",
        "import pytest\n@pytest.fixture\ndef db():\n    return 1\n",
    )
    _write(tmp_path, "test_app.py", "def test_x(db):\n    assert db\n")
    _write(tmp_path, "src/Foo.cs", "public class Foo { }\npublic interface IFoo { }\n")
    _write(
        tmp_path,
        "src/Startup.cs",
        "class Startup { void C(IServiceCollection s) { s.AddScoped<IFoo, Foo>(); } }\n",
    )
    return tmp_path


def _walked_files(root: Path) -> list[str]:
    snap = WalkSnapshot(root, prune_dirs=PRUNED_DIRS_DERIVED)
    return [
        p.relative_to(root).as_posix() for p in snap.iter_glob(root, "*") if p.is_file()
    ]


class TestFileFeedEquivalence:
    def test_full_list_produces_identical_edges(self, repo: Path) -> None:
        walked = HintRegistry().extract_all(repo)
        fed = HintRegistry().extract_all(repo, file_paths=_walked_files(repo))
        assert fed == walked
        # Sanity: the fixture actually produces edges for several extractors.
        sources = {e.hint_source for e in walked}
        assert "django_settings" in sources
        assert "node_package" in sources
        assert "pytest_conftest" in sources
        assert any(s.startswith("dotnet") for s in sources)

    def test_excluded_files_emit_no_edges(self, repo: Path) -> None:
        # On-disk files the index would exclude: generated partial defining a
        # DI-registered type, and a gitignored local settings module.
        _write(repo, "src/Bar.Designer.cs", "public partial class Bar { }\n")
        _write(
            repo,
            "src/Reg.cs",
            "class Reg { void C(IServiceCollection s) { s.AddScoped<Bar>(); } }\n",
        )
        _write(repo, "config/settings/local_settings.py", 'INSTALLED_APPS = ["myapp"]\n')

        walked = HintRegistry().extract_all(repo)
        walked_refs = {e.source for e in walked} | {e.target for e in walked}
        # The registry's own walk still sees both files (this is the bug the
        # file feed fixes); if this stops holding, the fixture needs updating.
        assert "src/Bar.Designer.cs" in walked_refs
        assert "config/settings/local_settings.py" in walked_refs

        indexed = [
            p for p in _walked_files(repo)
            if p not in ("src/Bar.Designer.cs", "config/settings/local_settings.py")
        ]
        fed = HintRegistry().extract_all(repo, file_paths=indexed)
        fed_refs = {e.source for e in fed} | {e.target for e in fed}
        assert "src/Bar.Designer.cs" not in fed_refs
        assert "config/settings/local_settings.py" not in fed_refs
        # Everything else is untouched (both lists share extract_all's
        # deterministic sort, so filtering preserves comparability).
        assert fed == [
            e
            for e in walked
            if "Bar.Designer" not in e.target + e.source
            and "local_settings" not in e.target + e.source
        ]
