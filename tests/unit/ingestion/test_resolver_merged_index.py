"""Unit tests for Tier-2b merged-import-symbol resolution and the
trait-dispatch global method index (perf refactor: O(imports) scan → O(1)
lookup with deterministic precedence).

Destination: tests/unit/ingestion/test_resolver_merged_index.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.heritage_resolver import HeritageResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        fi = _file_info(rel, abs_, lang)
        out[rel] = parse_file(fi, content.encode("utf-8"))
    return out


def _resolve(parsed, tmp_path, import_targets):
    resolver = CallResolver(parsed, import_targets, repo_path=str(tmp_path))
    edges = []
    for path, pf in parsed.items():
        for rc in resolver.resolve_file(path, pf.calls):
            edges.append((rc.caller_id, rc.callee_id, rc.confidence))
    return edges


class TestTier2bFreeCall:
    """Tier-2b: name not import-bound, but defined in an imported file."""

    def test_resolves_via_import_targets(self, tmp_path: Path) -> None:
        # caller.py has no parseable import binding for `helper` (the
        # import_targets edge is supplied directly, mimicking a resolved
        # wildcard/module import), so Tier-2a misses and Tier-2b must hit.
        files = {
            "lib.py": ("python", "def helper():\n    return 1\n"),
            "caller.py": ("python", "def run():\n    return helper()\n"),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(parsed, tmp_path, {"caller.py": {"lib.py"}, "lib.py": set()})
        assert ("caller.py::run", "lib.py::helper", 0.85) in edges, edges

    def test_external_imports_skipped(self, tmp_path: Path) -> None:
        files = {
            "caller.py": ("python", "def run():\n    return helper()\n"),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(parsed, tmp_path, {"caller.py": {"external:numpy"}})
        assert edges == [], edges

    def test_shadowed_name_resolves_deterministically(self, tmp_path: Path) -> None:
        """Two imported files export the same name → sorted-path-first wins.

        (Pre-refactor this iterated a set → hash-randomized winner; the
        merged index pins the winner to the lexicographically first path.)
        """
        files = {
            "a_lib.py": ("python", "def helper():\n    return 'a'\n"),
            "z_lib.py": ("python", "def helper():\n    return 'z'\n"),
            "caller.py": ("python", "def run():\n    return helper()\n"),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(
            parsed,
            tmp_path,
            {"caller.py": {"z_lib.py", "a_lib.py"}, "a_lib.py": set(), "z_lib.py": set()},
        )
        tier2b = [e for e in edges if e[2] == 0.85]
        assert tier2b == [("caller.py::run", "a_lib.py::helper", 0.85)], edges


class TestTier2bMemberCall:
    def test_class_method_in_imported_file(self, tmp_path: Path) -> None:
        files = {
            "models.py": (
                "python",
                "class User:\n    def save(self):\n        return 1\n",
            ),
            "caller.py": (
                "python",
                "def run():\n    return User.save()\n",
            ),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(parsed, tmp_path, {"caller.py": {"models.py"}, "models.py": set()})
        assert ("caller.py::run", "models.py::User::save", 0.88) in edges, edges

    def test_shadowed_method_pair_deterministic(self, tmp_path: Path) -> None:
        files = {
            "a_mod.py": ("python", "class User:\n    def save(self):\n        return 1\n"),
            "z_mod.py": ("python", "class User:\n    def save(self):\n        return 2\n"),
            "caller.py": ("python", "def run():\n    return User.save()\n"),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(
            parsed,
            tmp_path,
            {"caller.py": {"z_mod.py", "a_mod.py"}, "a_mod.py": set(), "z_mod.py": set()},
        )
        hits = [e for e in edges if e[2] == 0.88]
        assert hits == [("caller.py::run", "a_mod.py::User::save", 0.88)], edges


class TestTraitDispatchGlobalIndex:
    """Strategy 2b: (class, method) found in a NON-imported file → 0.75 edge."""

    def test_resolves_without_import_edge(self, tmp_path: Path) -> None:
        files = {
            "impls.py": ("python", "class Walker:\n    def visit(self):\n        return 1\n"),
            "caller.py": ("python", "def run():\n    return Walker.visit()\n"),
        }
        parsed = _parse_all(tmp_path, files)
        # no import_targets edge at all — forces the global fallback
        edges = _resolve(parsed, tmp_path, {p: set() for p in parsed})
        assert ("caller.py::run", "impls.py::Walker::visit", 0.75) in edges, edges

    def test_same_file_occurrence_skipped(self, tmp_path: Path) -> None:
        """The global fallback must skip the caller's own file (the same-file
        case is Tier-1/Strategy-2 territory and was already missed there)."""
        files = {
            # `visit` is nested under a DIFFERENT class in caller.py, so the
            # same-file (receiver, method) lookup misses; the global index
            # must not return caller.py's own entry for another class either.
            "caller.py": (
                "python",
                "class Other:\n    def helper(self):\n        return 1\n"
                "def run():\n    return Walker.visit()\n",
            ),
            "impls.py": ("python", "class Walker:\n    def visit(self):\n        return 1\n"),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve(parsed, tmp_path, {p: set() for p in parsed})
        assert ("caller.py::run", "impls.py::Walker::visit", 0.75) in edges, edges


class TestHeritageTier2b:
    def test_parent_in_imported_file(self, tmp_path: Path) -> None:
        files = {
            "base.py": ("python", "class Base:\n    pass\n"),
            "child.py": ("python", "class Child(Base):\n    pass\n"),
        }
        parsed = _parse_all(tmp_path, files)
        resolver = HeritageResolver(parsed, {"child.py": {"base.py"}, "base.py": set()})
        results = []
        for path, pf in parsed.items():
            results.extend(resolver.resolve_file(path, pf.heritage))
        hits = [(r.child_id, r.parent_id, r.confidence) for r in results]
        # Tier 3 (global unique) would also find Base at 0.50 — assert the
        # 2b path wins with its higher confidence.
        assert ("child.py::Child", "base.py::Base", 0.85) in hits, hits

    def test_shadowed_parent_deterministic(self, tmp_path: Path) -> None:
        files = {
            "a_base.py": ("python", "class Base:\n    pass\n"),
            "z_base.py": ("python", "class Base:\n    pass\n"),
            "child.py": ("python", "class Child(Base):\n    pass\n"),
        }
        parsed = _parse_all(tmp_path, files)
        resolver = HeritageResolver(
            parsed,
            {"child.py": {"z_base.py", "a_base.py"}, "a_base.py": set(), "z_base.py": set()},
        )
        results = []
        for path, pf in parsed.items():
            results.extend(resolver.resolve_file(path, pf.heritage))
        hits = [(r.child_id, r.parent_id, r.confidence) for r in results if r.confidence == 0.85]
        assert hits == [("child.py::Child", "a_base.py::Base", 0.85)], hits
